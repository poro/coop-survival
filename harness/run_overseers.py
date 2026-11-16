"""Stage 4: every overseer tier judges every report, through the Message
Batches API. Resumable: judgements already on disk are skipped, and a batch
that was submitted but not collected is picked up again.

  uv run python -m harness.run_overseers --profile dev --smoke
  uv run python -m harness.run_overseers --profile run --smoke
  uv run python -m harness.run_overseers --profile run --full
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
import time
from pathlib import Path

from harness.backends import (
    AnthropicClient, UsageMeter, fill, git_commit, ledger_append, ledger_total, load_models_config,
    load_prompts, prompt_hash,
)
from harness.env import load_config, load_params
from harness.overseer import parse_verdict, render_case, render_overseer_system
from harness.run_episodes import LEDGER, out_dir, write_json


def load_cases(episode_dir: Path) -> list[dict]:
    cases = []
    for path in sorted(episode_dir.glob("*.json")):
        ep = json.loads(path.read_text(encoding="utf-8"))
        if ep["status"] != "ok":
            continue
        for agent_id, agent in ep["agents"].items():
            if agent.get("report") is not None:
                cases.append({
                    "episode_id": ep["id"], "agent": agent_id, "stage": ep["stage"], "pressure": ep["pressure"],
                    "report": agent["report"], "partner_view": ep["partner_views"][agent_id],
                    "truth_broke_agreement": agent["labels"]["broke_agreement"],
                })
    return cases


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", choices=("dev", "run"), required=True)
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--direct", action="store_true", help="debugging only: ordinary calls instead of a batch")
    ap.add_argument("--poll", type=int, default=30, help="seconds between batch status checks")
    args = ap.parse_args(argv)
    if args.smoke == args.full:
        ap.error("choose exactly one of --smoke or --full")

    config, models = load_config(), load_models_config()
    params, prompts = load_params(), load_prompts()
    meter = UsageMeter(models)
    client = AnthropicClient(models, meter)
    base = out_dir(args.profile, args.smoke)
    judgements_path, state_path = base / "judgements.jsonl", base / "overseer_batch.json"
    tiers = {tier: models["models"][key]["id"] for tier, key in models["profiles"][args.profile]["overseers"].items()}
    max_tokens = models["request"]["max_tokens"]["verdict"]
    commit, p_hash = git_commit(), prompt_hash()

    done = set()
    if judgements_path.exists():
        for line in judgements_path.read_text(encoding="utf-8").splitlines():
            j = json.loads(line)
            done.add((j["episode_id"], j["agent"], j["tier"]))

    work = {}
    for case in load_cases(base / "episodes"):
        for tier, model in tiers.items():
            if (case["episode_id"], case["agent"], tier) in done:
                continue
            system = render_overseer_system(prompts, params, case["pressure"])
            user = render_case(case["report"], case["partner_view"])
            custom_id = f"{case['episode_id']}__{case['agent']}__{tier}"
            work[custom_id] = {"case": case, "tier": tier, "model": model, "system": system, "user": user}
    spent_before = ledger_total(LEDGER)
    print(f"{len(done)} judgements on disk, {len(work)} to make -> {judgements_path}")
    if not work:
        return 0
    if spent_before >= config["spend_stop_usd"]:
        print(f"spend ${spent_before:.2f} is past the stop line; not starting")
        return 1

    def params_for(w, messages=None):
        return client.params(model=w["model"], system=w["system"], max_tokens=max_tokens,
                             messages=messages or [{"role": "user", "content": w["user"]}])

    batch_id = None
    try:
        if args.direct:
            results = {cid: client.complete(model=w["model"], system=w["system"], max_tokens=max_tokens,
                                            messages=[{"role": "user", "content": w["user"]}])
                       for cid, w in work.items()}
        else:
            if state_path.exists():
                batch_id = json.loads(state_path.read_text())["batch_id"]
                print(f"resuming batch {batch_id}")
            else:
                batch_id = client.batch_submit([{"custom_id": cid, "params": params_for(w)} for cid, w in work.items()])
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
                            # One retry, as an ordinary call, with the parse error appended.
                            messages = [{"role": "user", "content": w["user"]}]
                            if first.content:
                                messages.append({"role": "assistant", "content": first.content})
                            messages.append({"role": "user", "content": fill(prompts["retry"], error=str(e))})
                            second = client.complete(model=w["model"], system=w["system"],
                                                     max_tokens=max_tokens, messages=messages)
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
                    "episode_id": case["episode_id"], "agent": case["agent"], "stage": case["stage"],
                    "tier": w["tier"], "model": w["model"], "status": "ok" if verdict else "invalid",
                    "invalid_reason": reason, "verdict": verdict,
                    "truth_broke_agreement": case["truth_broke_agreement"],
                    "system": w["system"], "user": w["user"],
                    "completions": [{"content": c.content_json, "stop_reason": c.stop_reason, "usage": c.usage,
                                     "cost_usd": c.cost_usd, "request_id": c.request_id, "error": c.error}
                                    for c in completions],
                    "batch_id": batch_id, "request_settings": client.request_settings(),
                    "prompt_hash": p_hash, "git_commit": commit, "profile": args.profile,
                }, sort_keys=True) + "\n")
        if state_path.exists():
            state_path.unlink()
        print(f"done: {counts}")
    finally:
        if meter.totals:
            ledger_append(LEDGER, {
                "script": "run_overseers", "profile": args.profile, "smoke": args.smoke,
                "at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
                "cost_usd": meter.total_cost, "by_model": meter.totals,
            })
        print(meter.summary())
        print(f"  total spent, all runs: ${spent_before + meter.total_cost:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
