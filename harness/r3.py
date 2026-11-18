"""Replication run: round two's conditions around the breaking point, on fresh
seeds. Episodes are played by round two's own `run_episode`, unchanged. Writes
only under data/r3/.

  uv run python -m harness.r3 --profile dev         (plumbing check, 2 seeds per cell)
  uv run python -m harness.r3 --profile run --full  (600 episodes; needs Mark's written approval)
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import yaml

from harness.backends import (
    ROOT, AnthropicClient, UsageMeter, git_commit, git_uncommitted, ledger_append, ledger_total, load_models_config,
    load_prompts, prompt_hash,
)
from harness.env import load_params
from harness.r2 import run_episode
from harness.run_episodes import write_json

R3_CONFIG = ROOT / "config" / "round3.yaml"
R3_DATA = ROOT / "data" / "r3"
LEDGER = R3_DATA / "spend.jsonl"


def load_r3_config() -> dict:
    with open(R3_CONFIG, encoding="utf-8") as f:
        return yaml.safe_load(f)


def base_dir(profile: str) -> Path:
    return R3_DATA / ("dev" if profile == "dev" else "full")


def plan(config: dict, dev: bool = False) -> list[dict]:
    first = config["seed_first"]
    last = first + config["dev_seeds"] - 1 if dev else config["seed_last"]
    specs = []
    for name, cell in config["cells"].items():
        for seed in range(first, last + 1):
            specs.append({
                "id": f"r3_{name}_seed{seed:03d}", "r3_cell": name, "experiment": cell["experiment"],
                "cell": f"{name}_s{round(cell['storm'] * 100)}_{cell['objective']}", "seed": seed,
                "storm": cell["storm"], "objective": cell["objective"], "agent": cell["agent"],
                "partner": "cooperator", "label": config["label"], "case_type": None,
            })
    return specs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", choices=("dev", "run"), required=True)
    ap.add_argument("--full", action="store_true", help="required with --profile run")
    args = ap.parse_args(argv)
    if (args.profile == "run") != args.full:
        ap.error("use --profile dev for the plumbing check, or --profile run --full for the replication")

    config, models = load_r3_config(), load_models_config()
    p_hash = prompt_hash()
    if not p_hash.startswith(config["expected_prompt_hash"]):
        print(f"STOP: prompt hash {p_hash[:12]} differs from round two's {config['expected_prompt_hash']}")
        return 1
    meter = UsageMeter(models)
    ctx = {"params": load_params(), "prompts": load_prompts(), "models": models, "profile_name": args.profile,
           "client": AnthropicClient(models, meter), "git_commit": git_commit(), "uncommitted_files": git_uncommitted(), "prompt_hash": p_hash}
    out = base_dir(args.profile) / "episodes"
    out.mkdir(parents=True, exist_ok=True)
    specs = plan(config, dev=args.profile == "dev")
    todo = [s for s in specs if not (out / f"{s['id']}.json").exists()]
    spent_before, stop_at = ledger_total(LEDGER), config["spend_stop_usd"]
    print(f"prompt hash {p_hash[:12]} matches round two")
    print(f"{len(specs)} episodes planned, {len(specs) - len(todo)} already on disk, {len(todo)} to run -> {out}")
    print(f"replication spend so far: ${spent_before:.2f}; cap ${stop_at}")

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
        if meter.totals:
            ledger_append(LEDGER, {
                "script": "r3_episodes", "profile": args.profile,
                "at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
                "cost_usd": meter.total_cost, "by_model": meter.totals,
            })
        print(f"done: {counts}")
        print(meter.summary())
        print(f"  replication spend, all runs: ${spent_before + meter.total_cost:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
