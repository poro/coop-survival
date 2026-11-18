import json

from harness.backends import ROOT
from harness.replay import build, compact, main, thinking_by_turn

R3_EPISODE = ROOT / "data" / "r3" / "full" / "episodes" / "r3_R1_seed050.json"
R1_EPISODE = ROOT / "data" / "episodes" / "s3_control_risk_seed00.json"


def embedded(html: str) -> list[dict]:
    start = html.index('<script id="data" type="application/json">') + len('<script id="data" type="application/json">')
    end = html.index("</script>", start)
    return json.loads(html[start:end])


def test_compact_keeps_the_log_round_by_round():
    ep = compact(json.loads(R3_EPISODE.read_text()))
    assert ep["study_round"] == "Replication"
    assert ep["opening"]["round"] == 0 and ep["opening"]["reserve"] == 4
    assert [r["round"] for r in ep["rounds"]] == [1]
    assert ep["rounds"][0]["storm"] and ep["rounds"][0]["actions"]["a"] == {"cells": 4, "n": 4, "type": "take"}
    assert ep["final"]["end_reason"] == "storm" and ep["final"]["survived"] == {"a": True, "b": False}
    a = ep["agents"]["a"]
    assert a["labels"]["broke_agreement"] and a["report"]["followed_agreement"] is False
    assert set(a["thinking"]) == {"0", "1"}  # this report call returned no thinking block
    assert "thinking" not in ep["agents"]["b"]


def test_compact_reads_round_one_episodes():
    ep = compact(json.loads(R1_EPISODE.read_text()))
    assert ep["study_round"] == "Round one" and ep["condition"].startswith("Stage 3")
    assert ep["agents"]["a"]["control"] is True
    assert len(ep["rounds"]) == ep["final"]["rounds_played"]


def test_a_retried_call_does_not_shift_later_rounds():
    log = [
        {"round": 0, "messages": {"a": {"text": "hello"}}},
        {"round": 1, "messages": {"a": {"text": "operating"}}},
        {"round": 2, "messages": {"a": {"text": "taking now"}}},
        {"final": True},
    ]
    call = lambda purpose, thought, text: {"purpose": purpose, "completion": [
        {"type": "thinking", "thinking": thought}, {"type": "text", "text": text}]}
    agent = {"calls": [
        call("turn", "t0", '{"message": "hello"}'),
        call("turn", "bad", "not json"),                  # failed reply for round 1
        call("turn", "t1", '{"message": "operating"}'),    # its retry
        call("turn", "t2", '{"message": "taking now"}'),
        call("report", "tr", "{}"),
    ]}
    assert thinking_by_turn(agent, log, "a") == {"0": "t0", "1": "t1", "2": "t2", "report": "tr"}


def test_message_text_cannot_close_the_script_element():
    ep = compact(json.loads(R3_EPISODE.read_text()))
    ep["opening"]["messages"]["a"]["text"] = "</script><script>alert(1)</script>"
    html = build([ep])
    assert html.count("</script>") == 2  # the data element's and the page script's own
    assert embedded(html)[0]["opening"]["messages"]["a"]["text"] == "</script><script>alert(1)</script>"


def test_cli_filters_to_broken_agreements(tmp_path):
    out = tmp_path / "replay.html"
    pattern = str(ROOT / "data" / "r3" / "full" / "episodes" / "r3_R1_seed05*.json")
    assert main([pattern, "--broke", "-o", str(out)]) == 0
    eps = embedded(out.read_text())
    assert eps and all(any(a.get("labels", {}).get("broke_agreement") for a in e["agents"].values()) for e in eps)
    assert main([str(R1_EPISODE), "--broke", "-o", str(tmp_path / "none.html")]) == 1


def test_broke_ignores_scripted_agents_that_break_by_design(tmp_path):
    scripted = str(ROOT / "data" / "r2" / "full" / "episodes" / "r2_F_blatant_seed0*.json")
    assert main([scripted, "--broke", "-o", str(tmp_path / "f.html")]) == 1
    assert main([scripted, "-o", str(tmp_path / "f.html")]) == 0
