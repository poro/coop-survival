"""Round eight: which part of round seven's notice deters breaking (the cost,
the reminder of the report, or both), at low and high effort, with oversight
of model reports. Writes only under data/r8/.

  uv run python -m harness.r8 episodes  --profile dev            (plumbing check, 2 per cell)
  uv run python -m harness.r8 overseers --profile dev
  uv run python -m harness.r8 episodes  --profile run --full     (1,150 episodes)
  uv run python -m harness.r8 overseers --profile run --full     (2,100 judgements, Message Batches)

Episodes are played by round two's `run_episode`. Both commands are
resumable and skip work already on disk. Each cell's report request and
objective are chosen by passing that cell's prompts and spec to `run_episode`.
Both commands stop with `STOPPED:` and exit code 2 at the first API error that
is not about a single episode (credit, authentication, permission, a rejected
request), or after several episodes in a row fail on API errors. Design:
PRD_ROUND8.md; predictions: PREREGISTRATION_R8.md.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import yaml

from harness.backends import (
    PROMPT_DIR, ROOT, AnthropicClient, UsageMeter, git_commit, git_uncommitted, ledger_append, ledger_total,
    load_models_config, prompt_hash,
)
from harness.consistency import verdict as consistency_verdict
from harness.env import load_params
from harness.game import GameParams
from harness.overseer import render_case, render_overseer_system
from harness.r2 import run_episode
from harness.r4 import R4_PROMPT_DIR, judge, load_r4_prompts, r4_prompt_hash
from harness.r5 import haiku_replay_specs
from harness.r6 import R6_PROMPT_DIR, r6_prompt_hash
from harness.r7 import R7_PROMPT_DIR, load_r7_prompts, r7_prompt_hash
from harness.run_episodes import write_json

R8_CONFIG = ROOT / "config" / "round8.yaml"
R8_PROMPT_DIR = PROMPT_DIR / "r8"
R8_DATA = ROOT / "data" / "r8"
LEDGER = R8_DATA / "spend.jsonl"
FULL_VIEW = "full_view"
REPORT_REQUESTS = {"round_two": "report_request", "received": "report_request_received",
                   "unverifiable": "report_request_unverifiable"}
STOP_EXIT = 2


class Stopped(Exception):
    """An API failure that is not about one episode: the run must stop, not record data."""


def load_r8_config() -> dict:
    with open(R8_CONFIG, encoding="utf-8") as f:
        return yaml.safe_load(f)


def hashes_unchanged(config: dict) -> str | None:
    """None if the four earlier prompt hashes are unchanged, otherwise why not."""
    if not prompt_hash().startswith(config["expected_r2_prompt_hash"]):
        return f"the round-two prompt hash is {prompt_hash()[:12]}, not {config['expected_r2_prompt_hash']}"
    if not r4_prompt_hash().startswith(config["expected_r4_prompt_hash"]):
        return f"the round-four prompt hash is {r4_prompt_hash()[:12]}, not {config['expected_r4_prompt_hash']}"
    if not r6_prompt_hash().startswith(config["expected_r6_prompt_hash"]):
        return f"the round-six prompt hash is {r6_prompt_hash()[:12]}, not {config['expected_r6_prompt_hash']}"
    if not r7_prompt_hash().startswith(config["expected_r7_prompt_hash"]):
        return f"the round-seven prompt hash is {r7_prompt_hash()[:12]}, not {config['expected_r7_prompt_hash']}"
    return None


def base_dir(profile: str) -> Path:
    return R8_DATA / ("dev" if profile == "dev" else "full")


def load_r8_prompts() -> dict[str, str]:
    """Round seven's prompts plus round eight's two objectives."""
    prompts = load_r7_prompts()
    for path in sorted(R8_PROMPT_DIR.glob("*.txt")):
        assert path.stem not in prompts, f"{path.name} would replace an earlier prompt"
        prompts[path.stem] = path.read_text(encoding="utf-8").strip("\n")
    return prompts


def r8_prompt_hash() -> str:
    """Hash over prompts/*.txt and prompts/r4, r6, r7 and r8/*.txt."""
    h = hashlib.sha256()
    paths = sorted(PROMPT_DIR.glob("*.txt"))
    for d in (R4_PROMPT_DIR, R6_PROMPT_DIR, R7_PROMPT_DIR, R8_PROMPT_DIR):
        paths += sorted(d.glob("*.txt"))
    for path in paths:
        h.update(str(path.relative_to(PROMPT_DIR)).encode())
        h.update(b"\0")
        h.update(path.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def stop_reason(exc: Exception) -> str:
    """A one-line description of an exception that stops the run."""
    msg = getattr(exc, "message", None) or str(exc)
    return f"{type(exc).__name__}: {msg}"[:400]


def is_api_error_episode(record: dict) -> bool:
    inv = record.get("invalid") or {}
    return record.get("status") != "ok" and inv.get("reason") == "api_error"


def prompts_for(prompts: dict, report: str) -> dict:
    """The prompts one cell's agents get: the same, except the report request."""
    out = dict(prompts)
    out["report_request"] = prompts[REPORT_REQUESTS[report]]
    return out


def plan(config: dict, dev: bool = False, cells=None) -> list[dict]:
    specs = []
    for name, cell in config["cells"].items():
        if cells and name not in cells:
            continue
        if cell.get("replay"):
            replay = [{**{k: v for k, v in r.items() if k != "r5_cell"}, "id": r["id"].replace("r5_HA_", "r8_HA_", 1),
                       "r8_cell": name, "report": cell["report"]} for r in haiku_replay_specs()]
            specs += replay[: config["dev_seeds"]] if dev else replay
            continue
        first = cell["seed_first"]
        last = first + config["dev_seeds"] - 1 if dev else cell["seed_last"]
        for seed in range(first, last + 1):
            specs.append({
                "id": f"r8_{name}_seed{seed:04d}", "r8_cell": name, "report": cell["report"], "experiment": cell["experiment"],
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
    config, models = load_r8_config(), load_models_config()
    problem = hashes_unchanged(config)
    if problem:
        print(f"STOP: {problem}")
        return 1
    meter = UsageMeter(models)
    p_hash = r8_prompt_hash()
    ctx = {"params": load_params(), "prompts": load_r8_prompts(), "models": models, "profile_name": args.profile,
           "client": AnthropicClient(models, meter), "git_commit": git_commit(),
           "uncommitted_files": git_uncommitted(), "prompt_hash": p_hash}
    out = base_dir(args.profile) / "episodes"
    out.mkdir(parents=True, exist_ok=True)
    cells = args.cells.split(",") if args.cells else None
    specs = plan(config, dev=args.profile == "dev", cells=cells)
    todo = [s for s in specs if not (out / f"{s['id']}.json").exists()]
    spent_before, stop_at = ledger_total(LEDGER), config["spend_stop_usd"]
    print(f"round-eight prompt hash {p_hash[:12]}; earlier hashes unchanged")
    print(f"{len(specs)} episodes planned, {len(specs) - len(todo)} already on disk, {len(todo)} to run -> {out}")
    print(f"round-eight spend so far: ${spent_before:.2f}; cap ${stop_at}")
    if ctx["uncommitted_files"]:
        print(f"uncommitted files: {ctx['uncommitted_files']}")

    def guarded(spec):
        if spent_before + meter.total_cost >= stop_at:
            return None
        record = run_episode(spec, {**ctx, "prompts": prompts_for(ctx["prompts"], spec["report"])})
        write_json(out / f"{spec['id']}.json", record)
        (out / f"{spec['id']}.log.jsonl").write_text(
            "".join(json.dumps(l, sort_keys=True, separators=(",", ":")) + "\n" for l in record["log"]),
            encoding="utf-8")
        return record

    counts = {"ok": 0, "invalid": 0, "skipped_for_budget": 0}
    stopped, in_a_row = None, 0
    try:
        pool = ThreadPoolExecutor(max_workers=config["workers"])
        try:
            futures = {pool.submit(guarded, s): s for s in todo}
            for i, fut in enumerate(as_completed(futures), 1):
                try:
                    record = fut.result()
                except Exception as e:  # raised by the client: credit, authentication, permission, bad request
                    stopped = f"{futures[fut]['id']}: {stop_reason(e)}"
                    break
                if record is None:
                    counts["skipped_for_budget"] += 1
                    continue
                counts[record["status"]] += 1
                note = "" if record["status"] == "ok" else f"  INVALID {record['invalid']}"
                print(f"[{i}/{len(todo)}] {record['id']} ${record['cost_usd']:.4f}{note}", flush=True)
                in_a_row = in_a_row + 1 if is_api_error_episode(record) else 0
                if in_a_row >= config["stop_after_api_errors_in_a_row"]:
                    stopped = f"{in_a_row} episodes in a row failed on API errors, last {record['id']}: {record['invalid']}"
                    break
        finally:
            pool.shutdown(wait=True, cancel_futures=True)
    finally:
        _ledger("r8_episodes", args.profile, meter)
        print(f"done: {counts}")
        print(meter.summary())
        print(f"  round-eight spend, all runs: ${spent_before + meter.total_cost:.2f}")
    if stopped:
        print(f"STOPPED: {stopped}")
        print("Episodes already written stay on disk; the next run resumes from them. Nothing may be scored yet.")
        return STOP_EXIT
    return 0


def load_cases(episode_dir: Path, overseen_cells) -> list[dict]:
    """One case per model report in the overseen cells, at evidence `full_view` (with the report)."""
    cases = []
    for path in sorted(episode_dir.glob("*.json")):
        ep = json.loads(path.read_text(encoding="utf-8"))
        if ep["status"] != "ok" or ep["r8_cell"] not in overseen_cells:
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
                "r8_cell": ep["r8_cell"], "case_type": None, "evidence": FULL_VIEW, "game_params": ep["game_params"],
                "params": params, "partner_view": view, "report": agent["report"],
                "truth_broke_agreement": lab["broke_agreement"], "truth_false_report": lab["false_report"],
                "truth_broken_pledge": lab["broken_pledge"], "truth_announced_take": lab["announced_take"],
                "consistency": consistency_verdict(params, agent["report"], view, FULL_VIEW),
            })
    return cases


def cmd_overseers(args) -> int:
    config, models, prompts = load_r8_config(), load_models_config(), load_r8_prompts()
    problem = hashes_unchanged(config)
    if problem:
        print(f"STOP: {problem}")
        return 1
    base = base_dir(args.profile)
    missing = [s["id"] for s in plan(config, dev=args.profile == "dev")
               if not (base / "episodes" / f"{s['id']}.json").exists()]
    if missing:
        print(f"STOP: {len(missing)} planned episodes are not on disk yet. Episodes run before judgements.")
        return STOP_EXIT
    meter = UsageMeter(models)
    client = AnthropicClient(models, meter)
    judgements_path, state_path = base / "judgements.jsonl", base / "overseer_batch.json"
    tiers = {tier: models["models"][key]["id"] for tier, key in models["profiles"][args.profile]["overseers"].items()}
    max_tokens = models["request"]["max_tokens"]["verdict"]
    commit, dirty, p_hash = git_commit(), git_uncommitted(), r8_prompt_hash()

    done = set()
    if judgements_path.exists():
        for line in judgements_path.read_text(encoding="utf-8").splitlines():
            j = json.loads(line)
            done.add((j["episode_id"], j["agent"], j["tier"]))
    work = {}
    for case in load_cases(base / "episodes", config["overseen_cells"]):
        system = render_overseer_system(prompts, case["params"], "risk", FULL_VIEW)
        user = render_case(case["report"], case["partner_view"], FULL_VIEW)
        for tier, model in tiers.items():
            if (case["episode_id"], case["agent"], tier) in done:
                continue
            custom_id = f"{case['episode_id']}__{case['agent']}__{tier}__fv"
            assert len(custom_id) <= 64, custom_id
            work[custom_id] = {"case": case, "tier": tier, "model": model, "system": system, "user": user}
    spent_before = ledger_total(LEDGER)
    print(f"{len(done)} judgements on disk, {len(work)} to make -> {judgements_path}")
    if not work:
        return 0
    if spent_before >= config["spend_stop_usd"]:
        print(f"round-eight spend ${spent_before:.2f} is past the cap; not starting")
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
                    **{k: case[k] for k in ("episode_id", "agent", "experiment", "cell", "r8_cell", "case_type",
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
    except Exception as e:  # raised by the client: credit, authentication, permission, bad request
        print(f"STOPPED: {stop_reason(e)}")
        print("Judgements already written stay on disk; the next run resumes the batch. Nothing may be scored yet.")
        return STOP_EXIT
    finally:
        _ledger("r8_overseers", args.profile, meter)
        print(meter.summary())
        print(f"  round-eight spend, all runs: ${spent_before + meter.total_cost:.2f}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=("episodes", "overseers"))
    ap.add_argument("--profile", choices=("dev", "run"), required=True)
    ap.add_argument("--full", action="store_true", help="required with --profile run")
    ap.add_argument("--cells", help="comma-separated subset of cells, e.g. P0,SN (episodes only)")
    ap.add_argument("--poll", type=int, default=30, help="seconds between batch status checks")
    args = ap.parse_args(argv)
    if (args.profile == "run") != args.full:
        ap.error("use --profile dev for the plumbing check, or --profile run --full for the real run")
    return cmd_episodes(args) if args.command == "episodes" else cmd_overseers(args)


if __name__ == "__main__":
    sys.exit(main())
