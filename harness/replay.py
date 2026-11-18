"""Episode replay viewer: one self-contained HTML page from episode files on disk.

Reads finished episode JSON files (any round) and writes a single HTML file that
replays each episode round by round: the shared reserve, both life-support
units, the generator, leaks and storms, every message, and each model agent's
report and its reasoning as summarised by the API. No model calls.

  uv run python -m harness.replay data/r3/full/episodes/r3_R1_seed050.json
  uv run python -m harness.replay "data/r3/full/episodes/*.json" --broke
  uv run python -m harness.replay "data/r2/full/episodes/r2_F_*.json" -o results/replay_f.html

The page shows the ground truth. The agents themselves never saw each
other's units or actions; the page says so.
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

from harness.backends import ROOT

TEMPLATE = Path(__file__).with_name("replay_template.html")
DEFAULT_OUT = ROOT / "results" / "replay.html"
PLACEHOLDER = "/*__EPISODES__*/[]"


def round_of(ep: dict) -> str:
    """Which round of the study an episode belongs to, from its id."""
    if ep["id"].startswith("r6_"):
        return "Round six"
    if ep["id"].startswith("r5_"):
        return "Round five"
    if ep["id"].startswith("r4_"):
        return "Round four"
    if ep["id"].startswith("r3_"):
        return "Replication"
    if ep["id"].startswith("r2_"):
        return "Round two"
    return "Round one"


def condition_of(ep: dict) -> str:
    if ep["id"].startswith("r6_"):
        return f"{ep['r6_cell']} · {ep['cell']} · report form {ep['report']}"
    if ep["id"].startswith("r5_"):
        return f"{ep['r5_cell']} · {ep['cell']}"
    if ep["id"].startswith("r4_"):
        return f"{ep['r4_cell']} · {ep['cell']}"
    if ep["id"].startswith("r3_"):
        return f"{ep['r3_cell']} · {ep['cell']}"
    if ep["id"].startswith("r2_"):
        return f"Experiment {ep['experiment']} · {ep['cell']}"
    stage = {1: "label experiment", 2: "live pair", 3: "model control", 5: "scripted control"}.get(ep["stage"], "")
    parts = [f"Stage {ep['stage']} {stage}".strip(), ep["label"], ep["pressure"]]
    return " · ".join(p for p in parts if p)


def produced(message: str, completion_text: str) -> bool:
    """Whether a completion's text contains this message, raw or JSON-escaped."""
    if not message:
        return False
    return message in completion_text or json.dumps(message, ensure_ascii=False)[1:-1] in completion_text


def thinking_by_turn(agent: dict, log: list[dict], agent_id: str) -> dict[str, str]:
    """Map each call's summarised thinking to the turn it produced.

    Keys are "0" for the opening message, "1".."n" for rounds, and "report".
    A turn call is matched to a round by the message text it produced, so a
    retried call does not shift later rounds; a later call for the same turn
    (the retry that succeeded) overwrites the earlier one.
    """
    out: dict[str, str] = {}
    turns = [l for l in log if not l.get("final")]
    next_turn = 0
    for call in agent.get("calls", []):
        blocks = call.get("completion") or []
        thought = " ".join(b.get("thinking", "") for b in blocks if b.get("type") == "thinking").strip()
        text = " ".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        if call.get("purpose") == "report":
            if thought:
                out["report"] = thought
            continue
        # The first turn at or after the expected one whose message this call
        # produced. A retried call matches the same turn as its retry.
        order = list(range(next_turn, len(turns))) + ([next_turn - 1] if next_turn > 0 else [])
        key = next((i for i in order if produced(turns[i]["messages"][agent_id]["text"], text)), next_turn)
        next_turn = key + 1
        if thought and key < len(turns):
            out[str(turns[key]["round"])] = thought
    return out


def agent_summary(agent: dict, agent_id: str, log: list[dict]) -> dict:
    keep = ("kind", "model", "effort", "label", "objective", "control", "case_type",
            "labels", "report", "consistency", "retries")
    out = {k: agent[k] for k in keep if k in agent}
    if agent.get("kind") == "model":
        out["thinking"] = thinking_by_turn(agent, log, agent_id)
    return out


def compact(ep: dict) -> dict:
    """The fields the viewer needs, from one episode record."""
    log = ep.get("log") or []
    turns = [l for l in log if not l.get("final")]
    final = next((l for l in log if l.get("final")), None)
    return {
        "id": ep["id"],
        "study_round": round_of(ep),
        "condition": condition_of(ep),
        "seed": ep["seed"],
        "status": ep["status"],
        "invalid": ep.get("invalid"),
        "pressure": ep.get("pressure"),
        "params": ep["game_params"],
        "agents": {i: agent_summary(a, i, log) for i, a in ep["agents"].items()},
        "opening": turns[0] if turns else None,
        "rounds": turns[1:],
        "final": final,
    }


def collect(patterns: list[str]) -> list[Path]:
    paths: list[Path] = []
    for pat in patterns:
        hits = sorted(glob.glob(pat)) or sorted(glob.glob(str(ROOT / pat)))
        for h in hits:
            p = Path(h)
            if p.suffix == ".json" and not p.name.endswith(".log.jsonl") and p not in paths:
                paths.append(p)
    return paths


def broke(ep: dict) -> bool:
    """Whether a model agent broke the agreement. Scripted agents that break it
    by design (round two's experiment F cases and experiment C's skimmer) do
    not count; select those episodes by file name instead."""
    return any(a.get("kind") == "model" and (a.get("labels") or {}).get("broke_agreement")
               for a in ep["agents"].values())


def build(episodes: list[dict]) -> str:
    template = TEMPLATE.read_text(encoding="utf-8")
    if PLACEHOLDER not in template:
        raise RuntimeError(f"placeholder missing from {TEMPLATE.name}")
    data = json.dumps(episodes, ensure_ascii=False, separators=(",", ":"))
    # Keep the JSON from closing the <script> element early.
    data = data.replace("</", "<\\/")
    return template.replace(PLACEHOLDER, data)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("episodes", nargs="+", help="episode .json files or glob patterns (quote globs)")
    ap.add_argument("-o", "--out", type=Path, default=DEFAULT_OUT, help=f"output HTML (default {DEFAULT_OUT.relative_to(ROOT)})")
    ap.add_argument("--broke", action="store_true", help="only episodes where a model agent broke the agreement")
    ap.add_argument("--limit", type=int, help="at most this many episodes")
    args = ap.parse_args(argv)

    paths = collect(args.episodes)
    if not paths:
        print("no episode files matched", file=sys.stderr)
        return 1
    episodes = []
    for p in paths:
        ep = json.loads(p.read_text(encoding="utf-8"))
        if args.broke and not broke(ep):
            continue
        episodes.append(compact(ep))
        if args.limit and len(episodes) >= args.limit:
            break
    if not episodes:
        print("no episodes left after filtering", file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(build(episodes), encoding="utf-8")
    size = args.out.stat().st_size / 1024
    print(f"{len(episodes)} episodes -> {args.out} ({size:,.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
