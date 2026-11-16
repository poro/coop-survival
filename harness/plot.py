"""The three figures, drawn from results/table.csv.

  uv run python -m harness.plot            (full run)
  uv run python -m harness.plot --smoke    (smoke test)
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent.parent
SURFACE, INK, MUTED, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"

LABEL_NAMES = {"same_model": "Same model", "other_ai": "Different AI model", "human": "Human"}
TIER_NAMES = {"small": "Haiku 5.5", "medium": "Sonnet 5.5", "large": "Opus 5.5"}


def read_table(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def num(row: dict, key: str):
    value = row.get(key, "")
    return None if value in ("", None) else float(value)


def new_axes(width=7.2, height=4.2):
    fig, ax = plt.subplots(figsize=(width, height), dpi=200)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(MUTED)
    ax.tick_params(colors=MUTED, length=0, labelsize=9)
    return fig, ax


def percent_axis(ax, horizontal=False):
    ticks = [0, 0.25, 0.5, 0.75, 1.0]
    names = ["0%", "25%", "50%", "75%", "100%"]
    if horizontal:
        ax.set_xlim(0, 1.0)
        ax.set_xticks(ticks, names)
        ax.xaxis.grid(True, color=GRID, linewidth=0.8)
    else:
        ax.set_ylim(0, 1.12)
        ax.set_yticks(ticks, names)
        ax.yaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def rate_bar(ax, x, row, name, color, width):
    """One bar with its Wilson interval and a k/n label. Returns False if there were no cases."""
    n = num(row, f"{name}_n")
    if not n:
        return False
    rate, lo, hi = num(row, name), num(row, f"{name}_lo"), num(row, f"{name}_hi")
    ax.bar(x, rate, width=width, color=color, edgecolor=SURFACE, linewidth=1.5, zorder=2)
    ax.errorbar(x, rate, yerr=[[rate - lo], [hi - rate]], color=INK, linewidth=1, capsize=3, zorder=3)
    # A marker in the series colour, so a rate of zero still shows which series it is.
    ax.plot([x], [rate], marker="o", markersize=6, color=color, markeredgecolor=SURFACE, markeredgewidth=1.2,
            zorder=4, clip_on=False)
    ax.text(x, hi + 0.025, f"{int(num(row, f'{name}_k'))}/{int(n)}", ha="center", va="bottom", fontsize=8, color=INK)
    return True


def title(fig, ax, heading, sub):
    fig.text(0.02, 0.965, heading, fontsize=11.5, color=INK, weight="bold", va="top")
    fig.text(0.02, 0.905, sub, fontsize=8.5, color=MUTED, va="top")


def fig1(rows, out):
    fig, ax = new_axes()
    stage1 = {r["condition"]: r for r in rows if r["stage"] == "1"}
    width = 0.32
    for i, label in enumerate(LABEL_NAMES):
        for offset, pressure, color in ((-width / 2 - 0.01, "safe", BLUE), (width / 2 + 0.01, "risk", ORANGE)):
            row = stage1.get(f"{label}/{pressure}")
            if row:
                rate_bar(ax, i + offset, row, "broken_agreement_rate", color, width)
    ax.set_xticks(range(len(LABEL_NAMES)), list(LABEL_NAMES.values()))
    ax.set_xlabel("What the agent was told its partner is", fontsize=9, color=MUTED)
    percent_axis(ax)
    handles = [plt.Rectangle((0, 0), 1, 1, color=BLUE), plt.Rectangle((0, 0), 1, 1, color=ORANGE)]
    ax.legend(handles, ["safe: no storms", "risk: 20% storm chance per round"], frameon=False, fontsize=8.5,
              loc="upper right", labelcolor=INK)
    title(fig, ax, "Broken agreements by partner label and pressure",
          "Stage 1, Opus 5.5 with a scripted cooperative partner. Share of episodes where the agent broke the agreement;\n"
          "labels show episodes broken / episodes, lines show 95% Wilson intervals.")
    fig.subplots_adjust(top=0.78, bottom=0.14, left=0.08, right=0.98)
    fig.savefig(out / "fig1_broken_agreements.png", facecolor=SURFACE)
    plt.close(fig)


def fig2(rows, out):
    groups = [("Stage 1: label experiment", "1"), ("Stage 2: live pairs", "2"),
              ("Stage 3: model control", "3"), ("Stage 5: scripted control", "5")]
    fig, ax = new_axes(7.2, 3.6)
    names = []
    for y, (name, stage) in enumerate(reversed(groups)):
        sel = [r for r in rows if r["stage"] == stage]
        broke = int(sum(num(r, "broken_agreement_rate_k") or 0 for r in sel))
        false = int(sum(num(r, "false_report_of_broken_k") or 0 for r in sel))
        reports = int(sum(num(r, "broken_agreement_rate_n") or 0 for r in sel))
        names.append(name)
        if not broke:
            ax.text(0.01, y, f"no broken agreements in {reports} reports", va="center", fontsize=8.5, color=MUTED)
            continue
        accurate = broke - false
        ax.barh(y, accurate / broke, height=0.5, color=BLUE, edgecolor=SURFACE, linewidth=1.5, zorder=2)
        ax.barh(y, false / broke, left=accurate / broke, height=0.5, color=ORANGE, edgecolor=SURFACE, linewidth=1.5, zorder=2)
        if accurate:
            ax.text(accurate / broke / 2, y, f"accurate {accurate}/{broke}", ha="center", va="center", fontsize=8, color="white")
        if false:
            ax.text(accurate / broke + false / broke / 2, y, f"false {false}/{broke}", ha="center", va="center", fontsize=8, color="white")
    ax.set_yticks(range(len(names)), names)
    ax.tick_params(axis="y", labelsize=9, colors=INK)
    ax.set_ylim(-0.6, len(names) - 0.4)
    percent_axis(ax, horizontal=True)
    handles = [plt.Rectangle((0, 0), 1, 1, color=BLUE), plt.Rectangle((0, 0), 1, 1, color=ORANGE)]
    ax.legend(handles, ["report matched the log", "report did not match the log"], frameon=False, fontsize=8.5,
              loc="lower center", bbox_to_anchor=(0.5, -0.34), ncol=2, labelcolor=INK)
    title(fig, ax, "Reports after a broken agreement",
          "Of the reports from agents that broke the agreement, the share that matched the ground-truth log.")
    fig.subplots_adjust(top=0.80, bottom=0.24, left=0.27, right=0.97)
    fig.savefig(out / "fig2_honesty.png", facecolor=SURFACE)
    plt.close(fig)


def fig3(rows, out):
    fig, ax = new_axes()
    tiers = {r["condition"].split("/")[0].replace("overseer_", ""): r for r in rows if r["stage"] == "4"}
    kinds = [("detection_scripted_control", "scripted control", BLUE),
             ("detection_model_control", "model control", ORANGE),
             ("detection_unprompted", "unprompted", AQUA)]
    width = 0.24
    empty = set()
    for i, tier in enumerate(TIER_NAMES):
        row = tiers.get(tier)
        if not row:
            continue
        for j, (name, _, color) in enumerate(kinds):
            if not rate_bar(ax, i + (j - 1) * (width + 0.02), row, name, color, width):
                empty.add(name)
        fa, n = num(row, "false_alarm_rate"), num(row, "false_alarm_rate_n")
        if n:
            ax.hlines(fa, i - 0.42, i + 0.42, color=INK, linewidth=1.6, linestyles=(0, (4, 2)), zorder=4)
            ax.text(i + 0.44, fa + 0.012, f"false alarms {int(num(row, 'false_alarm_rate_k'))}/{int(n)}",
                    ha="right", va="bottom", fontsize=7.5, color=INK)
    ax.set_xticks(range(len(TIER_NAMES)), [f"{v} overseer" for v in TIER_NAMES.values()])
    percent_axis(ax)
    handles = [plt.Rectangle((0, 0), 1, 1, color=c) for _, _, c in kinds] + [
        plt.Line2D([0], [0], color=INK, linewidth=1.6, linestyle=(0, (4, 2)))]
    names = [f"detected: {label}" + (" (no cases)" if name in empty else "") for name, label, _ in kinds]
    ax.legend(handles, names + ["false-alarm rate on kept agreements"], frameon=False, fontsize=8,
              loc="lower center", bbox_to_anchor=(0.5, -0.30), ncol=2, labelcolor=INK)
    title(fig, ax, "Overseer detection of broken agreements",
          "Share of broken agreements each overseer flagged, from the report and the partner's view of the episode.\n"
          "Labels show flagged / cases, lines show 95% Wilson intervals.")
    fig.subplots_adjust(top=0.78, bottom=0.22, left=0.08, right=0.98)
    fig.savefig(out / "fig3_detection.png", facecolor=SURFACE)
    plt.close(fig)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true")
    args = ap.parse_args(argv)
    out = ROOT / "data" / "smoke" / "results" if args.smoke else ROOT / "results"
    rows = read_table(out / "table.csv")
    fig1(rows, out)
    fig2(rows, out)
    fig3(rows, out)
    print(f"wrote fig1, fig2, fig3 to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
