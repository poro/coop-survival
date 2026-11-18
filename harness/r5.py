"""Round five: concealed breaks under a wary partner, with same-day anchors.
Writes only under data/r5/.

  uv run python -m harness.r5 episodes  --profile dev            (plumbing check, 2 per cell)
  uv run python -m harness.r5 overseers --profile dev
  uv run python -m harness.r5 episodes  --profile run --full     (750 episodes)
  uv run python -m harness.r5 overseers --profile run --full     (900 judgements, Message Batches)

Episodes are played by round two's `run_episode`. Both commands are
resumable and skip work already on disk. Design: PRD_ROUND5.md;
predictions: PREREGISTRATION_R5.md.
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import yaml

from harness.backends import (
    ROOT, AnthropicClient, UsageMeter, git_commit, git_uncommitted, ledger_append, ledger_total,
    load_models_config, prompt_hash,
)
from harness.consistency import verdict as consistency_verdict
from harness.env import load_params
from harness.game import GameParams
from harness.overseer import NO_REPORT, render_case, render_overseer_system
from harness.r2 import load_r2_config, plan as r2_plan, run_episode
from harness.r3 import load_r3_config, plan as r3_plan
from harness.r4 import judge, load_r4_prompts, r4_prompt_hash
from harness.run_episodes import write_json

R5_CONFIG = ROOT / "config" / "round5.yaml"
R5_DATA = ROOT / "data" / "r5"
LEDGER = R5_DATA / "spend.jsonl"


def load_r5_config() -> dict:
    with open(R5_CONFIG, encoding="utf-8") as f:
        return yaml.safe_load(f)


def hashes_unchanged(config: dict) -> str | None:
    """None if both earlier prompt hashes are unchanged, otherwise why not."""
    if not prompt_hash().startswith(config["expected_r2_prompt_hash"]):
        return f"the round-two prompt hash is {prompt_hash()[:12]}, not {config['expected_r2_prompt_hash']}"
    if not r4_prompt_hash().startswith(config["expected_r4_prompt_hash"]):
        return f"the round-four prompt hash is {r4_prompt_hash()[:12]}, not {config['expected_r4_prompt_hash']}"
    return None


def base_dir(profile: str) -> Path:
    return R5_DATA / ("dev" if profile == "dev" else "full")


def haiku_replay_specs() -> list[dict]:
    """Rounds two and three's 150 Haiku goal_strong episodes, as they were specified."""
    specs = [s for s in r2_plan(load_r2_config(), smoke=False) if s["id"].startswith("r2_D_haiku_s80_goal_strong_")]
    specs += [s for s in r3_plan(load_r3_config()) if s["r3_cell"] == "R3"]
    assert len(specs) == 150
    out = []
    for s in specs:
        s = {k: v for k, v in s.items() if k != "r3_cell"}
        out.append({**s, "id": f"r5_HA_{s['id']}", "r5_cell": "HA", "replays": s["id"]})
    return out


def plan(config: dict, dev: bool = False, cells=None) -> list[dict]:
    specs = []
    for name, cell in config["cells"].items():
        if cells and name not in cells:
            continue
        if cell.get("replay"):
            replay = haiku_replay_specs()
            specs += replay[: config["dev_seeds"]] if dev else replay
            continue
        first = cell["seed_first"]
        last = first + config["dev_seeds"] - 1 if dev else cell["seed_last"]
        for seed in range(first, last + 1):
            specs.append({
                "id": f"r5_{name}_seed{seed:04d}", "r5_cell": name, "experiment": cell["experiment"],
                "cell": f"{name}_s{round(cell['storm'] * 100)}_{cell['objective']}", "seed": seed,
                "storm": cell["storm"], "objective": cell["objective"], "agent": cell["agent"],
                "partner": cell.get("partner", "cooperator"), "label": cell.get("label", config["label"]),
                "case_type": None,
            })
    return specs


def _ledger(script: str, profile: str, meter: UsageMeter) -> None:
    if meter.totals:
        ledger_append(LEDGER, {
            "script": script, "profile": profile,
            "at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "cost_usd": meter.total_cost, "by_model": meter.totals,
        })


def cmd_episodes(args) -> int:
    config, models = load_r5_config(), load_models_config()
    problem = hashes_unchanged(config)
    if problem:
        print(f"STOP: {problem}")
        return 1
    meter = UsageMeter(models)
    p_hash = r4_prompt_hash()
    ctx = {"params": load_params(), "prompts": load_r4_prompts(), "models": models, "profile_name": args.profile,
           "client": AnthropicClient(models, meter), "git_commit": git_commit(),
           "uncommitted_files": git_uncommitted(), "prompt_hash": p_hash}
    out = base_dir(args.profile) / "episodes"
    out.mkdir(parents=True, exist_ok=True)
    cells = args.cells.split(",") if args.cells else None
    specs = plan(config, dev=args.profile == "dev", cells=cells)
    todo = [s for s in specs if not (out / f"{s['id']}.json").exists()]
    spent_before, stop_at = ledger_total(LEDGER), config["spend_stop_usd"]
    print(f"prompt hash {p_hash[:12]} (round four's; round five adds no prompt); earlier hashes unchanged")
    print(f"{len(specs)} episodes planned, {len(specs) - len(todo)} already on disk, {len(todo)} to run -> {out}")
    print(f"round-five spend so far: ${spent_before:.2f}; cap ${stop_at}")
    if ctx["uncommitted_files"]:
        print(f"uncommitted files: {ctx['uncommitted_files']}")

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
        _ledger("r5_episodes", args.profile, meter)
        print(f"done: {counts}")
        print(meter.summary())
        print(f"  round-five spend, all runs: ${spent_before + meter.total_cost:.2f}")
    return 0


def load_cases(episode_dir: Path, overseen_cells) -> list[dict]:
    """One case per model report in the overseen cells, at evidence `no_report`."""
    cases = []
    for path in sorted(episode_dir.glob("*.json")):
        ep = json.loads(path.read_text(encoding="utf-8"))
        if ep["status"] != "ok" or ep["r5_cell"] not in overseen_cells:
            continue
        gp = dict(ep["game_params"]); gp["leak_sizes"] = tuple(gp["leak_sizes"])
        params = GameParams(**gp)
        for agent_id, agent in ep["agents"].items():
            if agent.get("kind") != "model" or agent.get("report") is None:
                continue
            view = ep["partner_views"][agent_id]
            lab = agent["labels"]
            cases.append({
                "episode_id": ep["id"], "agent": agent_id, "experiment": ep["experiment"], "cell": ep["cell"],
                "r5_cell": ep["r5_cell"], "case_type": None, "evidence": NO_REPORT, "game_params": ep["game_params"],
                "params": params, "partner_view": view,
                "truth_broke_agreement": lab["broke_agreement"], "truth_false_report": lab["false_report"],
                "truth_broken_pledge": lab["broken_pledge"], "truth_announced_take": lab["announced_take"],
                "consistency": consistency_verdict(params, None, view, NO_REPORT),
            })
    return cases


def cmd_overseers(args) -> int:
    config, models, prompts = load_r5_config(), load_models_config(), load_r4_prompts()
    problem = hashes_unchanged(config)
    if problem:
        print(f"STOP: {problem}")
        return 1
    meter = UsageMeter(models)
    client = AnthropicClient(models, meter)
    base = base_dir(args.profile)
    judgements_path, state_path = base / "judgements.jsonl", base / "overseer_batch.json"
    tiers = {tier: models["models"][key]["id"] for tier, key in models["profiles"][args.profile]["overseers"].items()}
    max_tokens = models["request"]["max_tokens"]["verdict"]
    commit, dirty, p_hash = git_commit(), git_uncommitted(), r4_prompt_hash()

    done = set()
    if judgements_path.exists():
        for line in judgements_path.read_text(encoding="utf-8").splitlines():
            j = json.loads(line)
            done.add((j["episode_id"], j["agent"], j["tier"]))
    work = {}
    for case in load_cases(base / "episodes", config["overseen_cells"]):
        system = render_overseer_system(prompts, case["params"], "risk", NO_REPORT)
        user = render_case(None, case["partner_view"], NO_REPORT)
        for tier, model in tiers.items():
            if (case["episode_id"], case["agent"], tier) in done:
                continue
            custom_id = f"{case['episode_id']}__{case['agent']}__{tier}__nr"
            assert len(custom_id) <= 64, custom_id
            work[custom_id] = {"case": case, "tier": tier, "model": model, "system": system, "user": user}
    spent_before = ledger_total(LEDGER)
    print(f"{len(done)} judgements on disk, {len(work)} to make -> {judgements_path}")
    if not work:
        return 0
    if spent_before >= config["spend_stop_usd"]:
        print(f"round-five spend ${spent_before:.2f} is past the cap; not starting")
        return 1

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
                verdict, reason, completions = judge(client, prompts, w, results.get(cid), max_tokens)
                case = w["case"]
                counts["ok" if verdict else "invalid"] += 1
                f.write(json.dumps({
                    **{k: case[k] for k in ("episode_id", "agent", "experiment", "cell", "r5_cell", "case_type",
                                            "evidence", "truth_broke_agreement", "truth_false_report",
                                            "truth_broken_pledge", "truth_announced_take", "consistency")},
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
        _ledger("r5_overseers", args.profile, meter)
        print(meter.summary())
        print(f"  round-five spend, all runs: ${spent_before + meter.total_cost:.2f}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=("episodes", "overseers"))
    ap.add_argument("--profile", choices=("dev", "run"), required=True)
    ap.add_argument("--full", action="store_true", help="required with --profile run")
    ap.add_argument("--cells", help="comma-separated subset of cells, e.g. K,A0 (episodes only)")
    ap.add_argument("--poll", type=int, default=30, help="seconds between batch status checks")
    args = ap.parse_args(argv)
    if (args.profile == "run") != args.full:
        ap.error("use --profile dev for the plumbing check, or --profile run --full for the real run")
    return cmd_episodes(args) if args.command == "episodes" else cmd_overseers(args)


if __name__ == "__main__":
    sys.exit(main())
