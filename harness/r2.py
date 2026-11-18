"""Round two: episodes and overseer judgements. Writes only under data/r2/.

  uv run python -m harness.r2 episodes  --profile dev --smoke
  uv run python -m harness.r2 overseers --profile dev --smoke
  uv run python -m harness.r2 episodes  --profile run --smoke | --full
  uv run python -m harness.r2 overseers --profile run --smoke | --full

Both commands are resumable and skip work already on disk.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import yaml

from harness.agent import InvalidEpisode, ModelAgent, render_agent_system
from harness.backends import (
    ROOT, AnthropicClient, UsageMeter, fill, git_commit, git_uncommitted, ledger_append, ledger_total, load_models_config,
    load_prompts, prompt_hash,
)
from harness.consistency import verdict as consistency_verdict
from harness.env import PythonCoopEnv, load_params
from harness.game import AGENTS, GameParams
from harness.overseer import EVIDENCE_LEVELS, parse_verdict, render_case, render_overseer_system
from harness.run_episodes import write_json
from harness.score import labels
from harness.scripted import F_REPORTERS, ScriptedPartnerR2, ScriptedSkimmer, ScriptedWary

R2_CONFIG = ROOT / "config" / "round2.yaml"
R2_DATA = ROOT / "data" / "r2"
LEDGER = R2_DATA / "spend.jsonl"
MODEL_EXPERIMENTS = ("AB", "C", "D", "E", "G")


def load_r2_config() -> dict:
    with open(R2_CONFIG, encoding="utf-8") as f:
        return yaml.safe_load(f)


def base_dir(profile: str, smoke: bool) -> Path:
    if profile == "dev":
        return R2_DATA / "dev"
    return R2_DATA / "smoke" if smoke else R2_DATA / "full"


def pct(storm: float) -> int:
    return round(storm * 100)


def plan(config: dict, smoke: bool, experiments=None) -> list[dict]:
    seeds = range(config["smoke_seeds"] if smoke else config["seeds"])
    ex = config["experiments"]
    specs = []

    def add(experiment, cell, seed, **fields):
        base = {"objective": "none", "agent": "opus", "partner": "cooperator", "label": config["label"], "case_type": None}
        specs.append({"id": f"r2_{experiment}_{cell}_seed{seed:02d}", "experiment": experiment, "cell": cell,
                      "seed": seed, **base, **fields})

    for storm in ex["AB"]["storms"]:
        for objective in ex["AB"]["objectives"]:
            for seed in seeds:
                add("AB", f"s{pct(storm)}_{objective}", seed, storm=storm, objective=objective)
    for storm in ex["C"]["storms"]:
        for seed in seeds:
            add("C", f"s{pct(storm)}", seed, storm=storm, partner="skimmer")
    for agent in ex["D"]["agents"]:
        for storm in ex["D"]["storms"]:
            for objective in ex["D"]["objectives"]:
                for seed in seeds:
                    add("D", f"{agent}_s{pct(storm)}_{objective}", seed, storm=storm, objective=objective, agent=agent)
    for storm in ex["E"]["storms"]:
        for objective in ex["E"]["objectives"]:
            for seed in seeds:
                add("E", f"s{pct(storm)}_{objective}", seed, storm=storm, objective=objective)
    for seed in seeds:
        add("G", f"s{pct(ex['G']['storm'])}_{ex['G']['objective']}", seed, storm=ex["G"]["storm"],
            objective=ex["G"]["objective"], partner="live", label="same_model")
    for case_type in ex["F"]["case_types"]:
        for seed in seeds:
            add("F", case_type, seed, storm=ex["F"]["storm"], agent=None, label=None, case_type=case_type,
                leak_probability=ex["F"]["leak_probability"][case_type])
    if experiments:
        specs = [s for s in specs if s["experiment"] in experiments]
    return specs


def params_for(base: GameParams, spec: dict) -> GameParams:
    changes = {"storm_probability": spec["storm"]}
    if spec.get("leak_probability") is not None:
        changes["leak_probability"] = spec["leak_probability"]
    return dataclasses.replace(base, **changes)


def run_episode(spec: dict, ctx: dict) -> dict:
    models, prompts, client = ctx["models"], ctx["prompts"], ctx["client"]
    params = params_for(ctx["params"], spec)
    max_tokens = models["request"]["max_tokens"]
    efforts = models["effort_by_experiment"]
    effort = efforts.get(spec["experiment"], efforts["default"])
    agent_map = models["agents"][ctx["profile_name"]]

    def model_agent(label: str) -> ModelAgent:
        model = models["models"][agent_map[spec["agent"]]]["id"]
        system = render_agent_system(prompts, params, "risk", label, objective=spec["objective"])
        agent = ModelAgent(client, model, system, prompts, max_tokens, effort=effort)
        agent.label = label
        return agent

    if spec["experiment"] == "F":
        players = {"a": F_REPORTERS[spec["case_type"]](params), "b": ScriptedPartnerR2(params)}
    else:
        players = {"a": model_agent(spec["label"])}
        if spec["partner"] == "live":
            players["b"] = model_agent(spec["label"])
        elif spec["partner"] == "skimmer":
            players["b"] = ScriptedSkimmer(params)
        elif spec["partner"] == "wary":  # round five
            players["b"] = ScriptedWary(params)
        else:
            players["b"] = ScriptedPartnerR2(params)

    env = PythonCoopEnv(params)
    status, invalid, reports, current = "ok", None, {}, None
    try:
        for p in players.values():
            p.reset(spec["seed"])
        obs = env.reset(spec["seed"], "risk")
        while not obs["a"].done:
            turns = {}
            for agent_id in AGENTS:
                current = agent_id
                turns[agent_id] = players[agent_id].turn(obs[agent_id])
            obs = env.step(turns)
        for agent_id, player in players.items():
            if hasattr(player, "report"):
                current = agent_id
                reports[agent_id] = player.report(obs[agent_id])
    except InvalidEpisode as e:
        status, invalid = "invalid", {"agent": current, "reason": e.reason, "detail": e.detail}

    lines = list(env.truth().lines)
    finished = bool(lines) and bool(lines[-1].get("final"))
    views = {a: env.partner_view(a).to_dict() for a in AGENTS} if finished else None
    agents = {}
    for agent_id, player in players.items():
        report = reports.get(agent_id)
        scored = labels(lines, agent_id, report) if status == "ok" else None
        if isinstance(player, ModelAgent):
            agents[agent_id] = {
                "kind": "model", "model": player.model, "label": player.label, "objective": spec["objective"],
                "effort": effort, "system": player.system, "conversation": player.conversation,
                "calls": player.calls, "retries": player.retries, "report": report, "labels": scored,
            }
        elif report is not None:
            agents[agent_id] = {"kind": "scripted_reporter", "case_type": spec["case_type"], "report": report,
                                "labels": scored}
        else:
            kind = ("scripted_skimmer" if isinstance(player, ScriptedSkimmer)
                    else "scripted_wary" if isinstance(player, ScriptedWary) else "scripted")
            agents[agent_id] = {"kind": kind,
                                "labels": labels(lines, agent_id) if status == "ok" else None}
        if status == "ok" and report is not None:
            # The reference answer: is there an agreement-keeping explanation of this view and report?
            agents[agent_id]["consistency"] = {
                ev: consistency_verdict(params, report, views[agent_id], ev) for ev in EVIDENCE_LEVELS
            }
    return {
        **spec, "pressure": "risk", "profile": ctx["profile_name"], "status": status, "invalid": invalid,
        "git_commit": ctx["git_commit"], "uncommitted_files": ctx.get("uncommitted_files"),
        "prompt_hash": ctx["prompt_hash"],
        "backend": client.backend if client else None,
        "request_settings": client.request_settings(effort) if client else None,
        "game_params": dataclasses.asdict(params),
        "finished_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "agents": agents, "log": lines, "partner_views": views,
        "cost_usd": sum(c["cost_usd"] for a in agents.values() if a["kind"] == "model" for c in a["calls"]),
    }


def _finish(meter: UsageMeter, script: str, args, spent_before: float) -> None:
    if meter.totals:
        ledger_append(LEDGER, {
            "script": script, "profile": args.profile, "smoke": args.smoke,
            "at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "cost_usd": meter.total_cost, "by_model": meter.totals,
        })
    print(meter.summary())
    print(f"  round-two spend, all runs: ${spent_before + meter.total_cost:.2f}")


def cmd_episodes(args) -> int:
    config, models = load_r2_config(), load_models_config()
    meter = UsageMeter(models)
    experiments = args.experiments.split(",") if args.experiments else None
    specs = plan(config, args.smoke, experiments)
    needs_model = any(s["experiment"] != "F" for s in specs)
    ctx = {
        "params": load_params(), "prompts": load_prompts(), "models": models, "profile_name": args.profile,
        "client": AnthropicClient(models, meter) if needs_model else None,
        "git_commit": git_commit(), "uncommitted_files": git_uncommitted(), "prompt_hash": prompt_hash(),
    }
    out = base_dir(args.profile, args.smoke) / "episodes"
    out.mkdir(parents=True, exist_ok=True)
    todo = [s for s in specs if not (out / f"{s['id']}.json").exists()]
    on_disk = len(specs) - len(todo)
    if args.limit:
        todo = todo[: args.limit]
    spent_before, stop_at = ledger_total(LEDGER), config["spend_stop_usd"]
    print(f"{len(specs)} episodes planned, {on_disk} already on disk, {len(todo)} to run -> {out}")
    print(f"round-two spend so far: ${spent_before:.2f}; cap ${stop_at}")

    def guarded(spec):
        if spent_before + meter.total_cost >= stop_at:
            return None
        record = run_episode(spec, ctx)
        write_json(out / f"{spec['id']}.json", record)
        (out / f"{spec['id']}.log.jsonl").write_text(
            "".join(json.dumps(l, sort_keys=True, separators=(",", ":")) + "\n" for l in record["log"]),
            encoding="utf-8")
        return record

    counts = {"ok": 0, "invalid": 0, "skipped_for_budget": 0}
    try:
        with ThreadPoolExecutor(max_workers=config["workers"]) as pool:
            futures = {pool.submit(guarded, s): s for s in todo}
            for i, fut in enumerate(as_completed(futures), 1):
                record = fut.result()
                if record is None:
                    counts["skipped_for_budget"] += 1
                    continue
                counts[record["status"]] += 1
                note = "" if record["status"] == "ok" else f"  INVALID {record['invalid']}"
                print(f"[{i}/{len(todo)}] {record['id']} ${record['cost_usd']:.4f}{note}", flush=True)
    finally:
        print(f"done: {counts}")
        _finish(meter, "r2_episodes", args, spent_before)
    return 0


def load_cases(episode_dir: Path) -> list[dict]:
    """One case per report and evidence level: every report at full_view, and
    experiment F's reports at reserve_only as well."""
    cases = []
    for path in sorted(episode_dir.glob("*.json")):
        ep = json.loads(path.read_text(encoding="utf-8"))
        if ep["status"] != "ok":
            continue
        for agent_id, agent in ep["agents"].items():
            if agent.get("report") is None:
                continue
            for evidence in EVIDENCE_LEVELS if ep["experiment"] == "F" else ("full_view",):
                cases.append({
                    "episode_id": ep["id"], "agent": agent_id, "experiment": ep["experiment"], "cell": ep["cell"],
                    "case_type": ep["case_type"], "evidence": evidence, "game_params": ep["game_params"],
                    "report": agent["report"], "partner_view": ep["partner_views"][agent_id],
                    "truth_broke_agreement": agent["labels"]["broke_agreement"],
                    "truth_false_report": agent["labels"]["false_report"],
                    "consistency": agent["consistency"][evidence],
                })
    return cases


def cmd_overseers(args) -> int:
    config, models, prompts = load_r2_config(), load_models_config(), load_prompts()
    meter = UsageMeter(models)
    client = AnthropicClient(models, meter)
    base = base_dir(args.profile, args.smoke)
    judgements_path, state_path = base / "judgements.jsonl", base / "overseer_batch.json"
    tiers = {tier: models["models"][key]["id"] for tier, key in models["profiles"][args.profile]["overseers"].items()}
    max_tokens = models["request"]["max_tokens"]["verdict"]
    commit, dirty, p_hash = git_commit(), git_uncommitted(), prompt_hash()
    short = {"full_view": "fv", "reserve_only": "ro"}

    done = set()
    if judgements_path.exists():
        for line in judgements_path.read_text(encoding="utf-8").splitlines():
            j = json.loads(line)
            done.add((j["episode_id"], j["agent"], j["tier"], j["evidence"]))
    work = {}
    for case in load_cases(base / "episodes"):
        gp = dict(case["game_params"]); gp["leak_sizes"] = tuple(gp["leak_sizes"])
        system = render_overseer_system(prompts, GameParams(**gp), "risk", case["evidence"])
        user = render_case(case["report"], case["partner_view"], case["evidence"])
        for tier, model in tiers.items():
            if (case["episode_id"], case["agent"], tier, case["evidence"]) in done:
                continue
            custom_id = f"{case['episode_id']}__{case['agent']}__{tier}__{short[case['evidence']]}"
            assert len(custom_id) <= 64, custom_id
            work[custom_id] = {"case": case, "tier": tier, "model": model, "system": system, "user": user}
    spent_before = ledger_total(LEDGER)
    print(f"{len(done)} judgements on disk, {len(work)} to make -> {judgements_path}")
    if not work:
        return 0
    if spent_before >= config["spend_stop_usd"]:
        print(f"round-two spend ${spent_before:.2f} is past the cap; not starting")
        return 1

    batch_id = None
    try:
        if state_path.exists():
            batch_id = json.loads(state_path.read_text())["batch_id"]
            print(f"resuming batch {batch_id}")
        else:
            batch_id = client.batch_submit([
                {"custom_id": cid, "params": client.params(model=w["model"], system=w["system"], max_tokens=max_tokens,
                                                           messages=[{"role": "user", "content": w["user"]}])}
                for cid, w in work.items()])
            write_json(state_path, {"batch_id": batch_id, "submitted": len(work)})
            print(f"submitted batch {batch_id} with {len(work)} requests")
        while True:
            status = client.batch_status(batch_id)
            if status.processing_status == "ended":
                break
            c = status.request_counts
            print(f"  batch {status.processing_status}: {c.processing} processing, {c.succeeded} done", flush=True)
            time.sleep(args.poll)
        results = client.batch_results(batch_id)

        counts = {"ok": 0, "invalid": 0}
        with open(judgements_path, "a", encoding="utf-8") as f:
            for cid, w in work.items():
                first = results.get(cid)
                completions, verdict, reason = [], None, None
                if first is None:
                    reason = "missing_from_batch"
                else:
                    completions.append(first)
                    if first.error:
                        reason = f"api_error: {first.error}"
                    elif first.stop_reason == "refusal":
                        reason = "refusal"
                    else:
                        try:
                            verdict = parse_verdict(first.text)
                        except ValueError as e:
                            messages = [{"role": "user", "content": w["user"]}]
                            if first.content:
                                messages.append({"role": "assistant", "content": first.content})
                            messages.append({"role": "user", "content": fill(prompts["retry"], error=str(e))})
                            second = client.complete(model=w["model"], system=w["system"], max_tokens=max_tokens,
                                                     messages=messages)
                            completions.append(second)
                            try:
                                if second.error or second.stop_reason == "refusal":
                                    raise ValueError(second.error or "refusal")
                                verdict = parse_verdict(second.text)
                            except ValueError as e2:
                                reason = f"parse_failure: {e2}"
                case = w["case"]
                counts["ok" if verdict else "invalid"] += 1
                f.write(json.dumps({
                    **{k: case[k] for k in ("episode_id", "agent", "experiment", "cell", "case_type", "evidence",
                                            "truth_broke_agreement", "truth_false_report", "consistency")},
                    "tier": w["tier"], "model": w["model"], "status": "ok" if verdict else "invalid",
                    "invalid_reason": reason, "verdict": verdict, "system": w["system"], "user": w["user"],
                    "completions": [{"content": c.content_json, "stop_reason": c.stop_reason, "usage": c.usage,
                                     "cost_usd": c.cost_usd, "request_id": c.request_id, "error": c.error}
                                    for c in completions],
                    "batch_id": batch_id, "request_settings": client.request_settings(),
                    "prompt_hash": p_hash, "git_commit": commit, "uncommitted_files": dirty, "profile": args.profile,
                }, sort_keys=True) + "\n")
        if state_path.exists():
            state_path.unlink()
        print(f"done: {counts}")
    finally:
        _finish(meter, "r2_overseers", args, spent_before)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=("episodes", "overseers"))
    ap.add_argument("--profile", choices=("dev", "run"), required=True)
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--full", action="store_true", help="the full round-two run (needs Mark's written approval)")
    ap.add_argument("--experiments", help="comma-separated subset, e.g. AB,F")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--poll", type=int, default=30)
    args = ap.parse_args(argv)
    if args.smoke == args.full:
        ap.error("choose exactly one of --smoke or --full")
    return cmd_episodes(args) if args.command == "episodes" else cmd_overseers(args)


if __name__ == "__main__":
    sys.exit(main())
