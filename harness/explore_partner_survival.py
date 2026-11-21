"""Exploratory, not preregistered: would the partner have survived if the
agent had kept the agreement?

The same seed gives the same storm and leak schedule in every condition
(see harness/game.py), and the scripted cooperative partner always operates.
So for each break in a cell without the notice, the episode on the same seed
in the matching notice cell shows what happened when the agent kept the
agreement against the same storm. Pairs used:

  round seven:  R0 and RU breaks  ->  SN    (seeds 1150 to 1349)
  round eight:  P0 breaks         ->  SN    (seeds 1350 to 1549)
                PH breaks         ->  SNH   (seeds 1350 to 1449)

A pair counts only if the notice-cell episode is valid and its agent kept
the agreement. The script also checks the assumption: on seeds where the
agent kept the agreement in both cells, the episodes must end in the same
round for the same reason.

  uv run python -m harness.explore_partner_survival
"""

from __future__ import annotations

import json
from collections import Counter

from harness.backends import ROOT

PAIRS = [
    ("r7", "R0", "SN", range(1150, 1350)),
    ("r7", "RU", "SN", range(1150, 1350)),
    ("r8", "P0", "SN", range(1350, 1550)),
    ("r8", "PH", "SNH", range(1350, 1450)),
]


def _load(rnd: str, cell: str, seed: int) -> dict | None:
    path = ROOT / "data" / rnd / "full" / "episodes" / f"{rnd}_{cell}_seed{seed}.json"
    if not path.exists():
        return None
    ep = json.loads(path.read_text(encoding="utf-8"))
    return ep if ep.get("status") == "ok" and ep.get("log") else None


def _end(ep: dict) -> tuple:
    last = ep["log"][-1]
    return last["rounds_played"], last["end_reason"]


def pair_counts(rnd: str, cell: str, notice: str, seeds) -> dict:
    c = Counter()
    for seed in seeds:
        base, kept = _load(rnd, cell, seed), _load(rnd, notice, seed)
        if base is None or kept is None:
            c["pairs_missing_or_invalid"] += 1
            continue
        a_base, a_kept = base["agents"]["a"]["labels"], kept["agents"]["a"]["labels"]
        if not a_base["broke_agreement"]:
            if not a_kept["broke_agreement"]:
                c["kept_in_both"] += 1
                c["kept_in_both_same_ending"] += _end(base) == _end(kept)
            continue
        if a_kept["broke_agreement"]:
            c["notice_episode_also_broke"] += 1
            continue
        s_base, s_kept = base["log"][-1]["survived"], kept["log"][-1]["survived"]
        c["breaks"] += 1
        c["partner_died_after_break"] += not s_base["b"]
        c["agent_survived_after_break"] += s_base["a"]
        c["partner_survived_if_kept"] += s_kept["b"]
        c["agent_survived_if_kept"] += s_kept["a"]
        c[f"storm_after_round_{_end(kept)[0]}_if_kept"] += _end(kept)[1] == "storm"
    return dict(c)


def main() -> int:
    total = Counter()
    for rnd, cell, notice, seeds in PAIRS:
        c = pair_counts(rnd, cell, notice, seeds)
        print(f"{rnd} {cell} breaks vs {notice} on the same seed: {c}")
        total.update({k: v for k, v in c.items() if not k.startswith("storm_")})
    print("pooled:", dict(total))
    ok = total["kept_in_both"] == total["kept_in_both_same_ending"]
    print("same storm on the same seed:", "confirmed" if ok else "NOT confirmed")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
