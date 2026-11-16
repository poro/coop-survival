"""Stages 1 to 3: play episodes and write one JSON per episode. Resumable:
an episode already on disk is skipped, whether it ended ok or invalid.

  uv run python -m harness.run_episodes --profile dev --smoke
  uv run python -m harness.run_episodes --profile run --smoke
  uv run python -m harness.run_episodes --profile run --full
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path

from harness.agent import InvalidEpisode, ModelAgent, render_agent_system
from harness.backends import (
    ROOT, AnthropicClient, ModelClient, UsageMeter, git_commit, ledger_append, ledger_total,
    load_models_config, load_prompts, prompt_hash,
)
from harness.env import PythonCoopEnv, load_config, load_params
from harness.game import AGENTS
from harness.score import labels
from harness.scripted import ScriptedPartner

LEDGER = ROOT / "data" / "spend.jsonl"


def out_dir(profile: str, smoke: bool) -> Path:
    if profile == "dev":
        return ROOT / "data" / "dev"
    return ROOT / "data" / "smoke" if smoke else ROOT / "data"


def plan(config: dict, stages: list[int], smoke: bool) -> list[dict]:
    n = config["smoke"] if smoke else {f"stage{i}_seeds": config["seeds"] for i in (1, 2, 3)}
    specs = []
    if 1 in stages:
        for label in config["labels"]:
            for pressure in config["pressures"]:
                for seed in range(n["stage1_seeds"]):
                    specs.append({"id": f"s1_{label}_{pressure}_seed{seed:02d}", "stage": 1, "seed": seed,
                                  "pressure": pressure, "label": label, "partner": "scripted", "control": False})
    if 2 in stages:
        for pairing in ("same_model", "other_ai"):
            for seed in range(n["stage2_seeds"]):
                specs.append({"id": f"s2_{pairing}_risk_seed{seed:02d}", "stage": 2, "seed": seed,
                              "pressure": "risk", "label": pairing, "partner": pairing, "control": False})
    if 3 in stages:
        for seed in range(n["stage3_seeds"]):
            specs.append({"id": f"s3_control_risk_seed{seed:02d}", "stage": 3, "seed": seed, "pressure": "risk",
                          "label": config["control_label"], "partner": "scripted", "control": True})
    return specs


def write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def run_episode(spec: dict, ctx: dict) -> dict:
    params, prompts, models, profile = ctx["params"], ctx["prompts"], ctx["models"], ctx["profile"]
    client: ModelClient = ctx["client"]
    max_tokens = models["request"]["max_tokens"]
    model_id = lambda key: models["models"][key]["id"]

    def model_agent(model_key: str, label: str, control: bool) -> ModelAgent:
        system = render_agent_system(prompts, params, spec["pressure"], label, control)
        agent = ModelAgent(client, model_id(model_key), system, prompts, max_tokens)
        agent.label, agent.control = label, control
        return agent

    players = {"a": model_agent(profile["focal"], spec["label"], spec["control"])}
    if spec["partner"] == "scripted":
        players["b"] = ScriptedPartner(params)
    else:
        # Stage 2 labels are truthful: each agent is told what its partner is.
        players["b"] = model_agent(profile["partners"][spec["partner"]], spec["partner"], False)

    env = PythonCoopEnv(params)
    status, invalid, reports = "ok", None, {}
    current = None
    try:
        for p in players.values():
            p.reset(spec["seed"])
        obs = env.reset(spec["seed"], spec["pressure"])
        while not obs["a"].done:
            turns = {}
            for agent_id in AGENTS:
                current = agent_id
                turns[agent_id] = players[agent_id].turn(obs[agent_id])
            obs = env.step(turns)
        for agent_id, player in players.items():
            if isinstance(player, ModelAgent):
                current = agent_id
                reports[agent_id] = player.report(obs[agent_id])
    except InvalidEpisode as e:
        status, invalid = "invalid", {"agent": current, "reason": e.reason, "detail": e.detail}

    lines = list(env.truth().lines)
    finished = bool(lines) and bool(lines[-1].get("final"))
    agents = {}
    for agent_id, player in players.items():
        if isinstance(player, ModelAgent):
            agents[agent_id] = {
                "kind": "model", "model": player.model, "label": player.label, "control": player.control,
                "system": player.system, "conversation": player.conversation, "calls": player.calls,
                "retries": player.retries, "report": reports.get(agent_id),
                "labels": labels(lines, agent_id, reports.get(agent_id), control=player.control)
                if status == "ok" else None,
            }
        else:
            agents[agent_id] = {"kind": "scripted"}
    return {
        **spec,
        "profile": ctx["profile_name"], "status": status, "invalid": invalid,
        "git_commit": ctx["git_commit"], "prompt_hash": ctx["prompt_hash"], "backend": client.backend,
        "request_settings": client.request_settings(), "game_params": asdict(params),
        "finished_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "agents": agents, "log": lines,
        "partner_views": {a: env.partner_view(a).to_dict() for a in AGENTS} if finished else None,
        "cost_usd": sum(c["cost_usd"] for a in agents.values() if a["kind"] == "model" for c in a["calls"]),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", choices=("dev", "run"), required=True)
    ap.add_argument("--smoke", action="store_true", help="the 30-episode smoke subset")
    ap.add_argument("--full", action="store_true", help="the full 450-episode run (needs Mark's approval)")
    ap.add_argument("--stages", default="1,2,3")
    ap.add_argument("--limit", type=int, help="run at most this many episodes (debugging)")
    args = ap.parse_args(argv)
    if args.smoke == args.full:
        ap.error("choose exactly one of --smoke or --full")

    config, models = load_config(), load_models_config()
    meter = UsageMeter(models)
    ctx = {
        "params": load_params(), "prompts": load_prompts(), "models": models,
        "profile": models["profiles"][args.profile], "profile_name": args.profile,
        "client": AnthropicClient(models, meter), "git_commit": git_commit(), "prompt_hash": prompt_hash(),
    }
    out = out_dir(args.profile, args.smoke) / "episodes"
    out.mkdir(parents=True, exist_ok=True)
    specs = plan(config, [int(s) for s in args.stages.split(",")], args.smoke)
    todo = [s for s in specs if not (out / f"{s['id']}.json").exists()]
    on_disk = len(specs) - len(todo)
    if args.limit:
        todo = todo[: args.limit]
    spent_before = ledger_total(LEDGER)
    stop_at = config["spend_stop_usd"]
    print(f"{len(specs)} episodes planned, {on_disk} already on disk, {len(todo)} to run -> {out}")
    print(f"spent so far (all runs): ${spent_before:.2f}; stop line ${stop_at}")

    def guarded(spec):
        if spent_before + meter.total_cost >= stop_at:
            return None
        record = run_episode(spec, ctx)
        write_json(out / f"{spec['id']}.json", record)
        (out / f"{spec['id']}.log.jsonl").write_text(
            "".join(json.dumps(l, sort_keys=True, separators=(",", ":")) + "\n" for l in record["log"]),
            encoding="utf-8",
        )
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
        if meter.totals:
            ledger_append(LEDGER, {
                "script": "run_episodes", "profile": args.profile, "smoke": args.smoke,
                "at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
                "cost_usd": meter.total_cost, "by_model": meter.totals,
            })
        print(f"done: {counts}")
        print(meter.summary())
        print(f"  total spent, all runs: ${spent_before + meter.total_cost:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
