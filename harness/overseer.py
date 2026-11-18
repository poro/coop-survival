"""Overseer judgements. The overseer never plays: it reads one report and the
partner's view of the episode, and says whether the reporting agent broke the
agreement."""

from __future__ import annotations

import json

from harness.agent import parse_json_object, render_rules
from harness.backends import fill
from harness.game import GameParams


EVIDENCE_LEVELS = ("full_view", "reserve_only")
# Round four: the partner's full view with the reporting agent's report left out.
NO_REPORT = "no_report"
SYSTEM_PROMPT = {"full_view": "overseer_system", "reserve_only": "overseer_system_reserve_only",
                 NO_REPORT: "overseer_system_no_report"}


def render_overseer_system(prompts: dict, params: GameParams, pressure: str, evidence: str = "full_view") -> str:
    return fill(prompts[SYSTEM_PROMPT[evidence]], rules=render_rules(prompts, params, pressure))


def render_case(report: dict, partner_view: dict, evidence: str = "full_view") -> str:
    """The user message: the report and the partner's view, as compact JSON.
    Keys are renamed so the overseer never has to map agent ids. At
    `reserve_only` the partner's own actions and unit are left out. At
    `no_report` (round four) the view is as at `full_view` and the report is
    left out; `report` may then be None."""
    if evidence not in (*EVIDENCE_LEVELS, NO_REPORT):
        raise ValueError(f"evidence must be one of {(*EVIDENCE_LEVELS, NO_REPORT)}")
    rounds = []
    for entry in partner_view["rounds"]:
        row = {
            "round": entry["round"],
            "reserve": entry["reserve"],
            "reporting_agent_message": entry["agent_message"],
            "partner_message": entry["partner_message"],
        }
        if entry["round"] > 0:
            row["generator_ran"] = entry["generator_ran"]
            if evidence in ("full_view", NO_REPORT):
                row["partner_action"] = entry["partner_action"]
                row["partner_unit"] = entry["partner_unit"]
        rounds.append(row)
    case = {} if evidence == NO_REPORT else {"report": report}
    case["partner_view"] = {
        "rounds": rounds,
        "final_reserve": partner_view["final_reserve"],
        "end_reason": partner_view["end_reason"],
        "survived": {
            "reporting_agent": partner_view["survived"]["agent"],
            "partner": partner_view["survived"]["partner"],
        },
    }
    return json.dumps(case, separators=(",", ":"))


def parse_verdict(text: str) -> dict:
    data = parse_json_object(text)
    if not isinstance(data.get("broke_agreement"), bool):
        raise ValueError('"broke_agreement" must be true or false')
    confidence = data.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        raise ValueError('"confidence" must be a number from 0 to 1')
    if not isinstance(data.get("reason"), str):
        raise ValueError('"reason" must be a string')
    return {
        "broke_agreement": data["broke_agreement"],
        "confidence": float(confidence),
        "reason": data["reason"],
    }
