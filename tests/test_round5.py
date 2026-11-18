import json

from harness.env import PythonCoopEnv
from harness.scripted import WARY_PREEMPT, WARY_WARNING, FixedTurns, ScriptedPartnerR2, ScriptedWary, play_episode
from tests.conftest import opening, turn


def play(params, agent_turns, partner_cls, seed=3, pressure="safe"):
    env = PythonCoopEnv(params)
    return play_episode(env, {"a": FixedTurns(agent_turns), "b": partner_cls(params)}, seed, pressure).lines


def b_moves(lines):
    return [(l["actions"]["b"], l["messages"]["b"]) for l in lines if "actions" in l]


def test_wary_partner_matches_round_two_when_never_told_of_a_take(params):
    script = [opening()] + [turn("operate")] * 2 + [turn("take", 4)] + [turn("wait")] * (params.max_rounds - 3)
    wary, r2 = play(params, script, ScriptedWary), play(params, script, ScriptedPartnerR2)
    assert b_moves(wary) == b_moves(r2)
    assert wary[0]["messages"]["b"]["text"] == r2[0]["messages"]["b"]["text"] + " " + WARY_WARNING
    assert WARY_WARNING not in r2[0]["messages"]["b"]["text"]


def test_wary_partner_takes_first_after_a_take_pledge(params):
    script = [opening("take"), turn("take", 4, pledge="operate")] + [turn("wait")] * (params.max_rounds - 1)
    lines = play(params, script, ScriptedWary)
    first = lines[1]
    assert first["actions"]["b"]["type"] == "take" and first["actions"]["b"]["n"] == params.share
    assert first["messages"]["b"]["text"] == WARY_PREEMPT
    # The cooperative partner operates in the same spot.
    assert play(params, script, ScriptedPartnerR2)[1]["actions"]["b"]["type"] == "operate"


def test_wary_partner_does_not_preempt_after_an_operate_pledge(params):
    script = [opening("operate"), turn("take", 4, pledge="operate")] + [turn("wait")] * (params.max_rounds - 1)
    lines = play(params, script, ScriptedWary)
    assert lines[1]["actions"]["b"]["type"] == "operate"


def test_wary_partner_does_not_preempt_a_take_at_the_target(params, leak_free_seed):
    # Operate to 8, pledge take for the next round: that take keeps the agreement, so no race.
    script = [opening(), turn("operate"), turn("operate", pledge="take"), turn("take", 4)] + \
             [turn("wait")] * (params.max_rounds - 3)
    wary = play(params, script, ScriptedWary, seed=leak_free_seed)
    r2 = play(params, script, ScriptedPartnerR2, seed=leak_free_seed)
    assert wary[2]["reserve"] == params.reserve_target  # the pledge was made with the reserve at 8
    assert b_moves(wary) == b_moves(r2)
    assert WARY_PREEMPT not in [m["text"] for _, m in b_moves(wary)]


# -- the round-five plan and runner ------------------------------------------

from harness.backends import load_prompts, prompt_hash  # noqa: E402
from harness.r2 import load_r2_config, plan as r2_plan, run_episode  # noqa: E402
from harness.r3 import load_r3_config, plan as r3_plan  # noqa: E402
from harness.r4 import load_r4_prompts, r4_prompt_hash  # noqa: E402
from harness.r5 import haiku_replay_specs, hashes_unchanged, load_r5_config, plan  # noqa: E402
from tests.test_round2 import GOAL_STRONG, ctx  # noqa: E402


def r5_ctx(params):
    c = ctx(params)
    c["prompts"] = load_r4_prompts()
    return c


def test_plan_is_750_episodes():
    specs = plan(load_r5_config())
    assert len(specs) == 750 and len({s["id"] for s in specs}) == 750
    by_cell = {}
    for s in specs:
        by_cell.setdefault(s["r5_cell"], []).append(s["seed"])
    assert sorted(by_cell["K"]) == sorted(by_cell["A0"]) == list(range(250, 450))
    assert sorted(by_cell["KH"]) == sorted(by_cell["W2"]) == list(range(250, 350))
    assert len(by_cell["HA"]) == 150
    assert len(plan(load_r5_config(), dev=True)) == 10


def test_haiku_replay_is_exactly_rounds_two_and_three_specs():
    old = [s for s in r2_plan(load_r2_config(), smoke=False) if s["id"].startswith("r2_D_haiku_s80_goal_strong_")]
    old += [{k: v for k, v in s.items() if k != "r3_cell"} for s in r3_plan(load_r3_config()) if s["r3_cell"] == "R3"]
    new = haiku_replay_specs()
    strip = lambda s: {k: v for k, v in s.items() if k not in ("id", "r5_cell", "replays")}
    assert [strip(s) for s in new] == [strip(s) for s in old]
    assert [s["replays"] for s in new] == [s["id"] for s in old]


def test_no_prompt_changed_since_round_four():
    assert hashes_unchanged(load_r5_config()) is None
    assert set(load_r4_prompts()) - set(load_prompts()) == {
        "objective_goal_strong_p1", "objective_goal_strong_p2", "overseer_system_no_report"}


def test_cells_get_the_partner_effort_and_objective_the_prd_says(params):
    specs = {s["r5_cell"]: s for s in plan(load_r5_config(), dev=True)}
    runs = {c: run_episode(specs[c], r5_ctx(params)) for c in ("K", "KH", "A0", "W2", "HA")}
    assert runs["K"]["agents"]["b"]["kind"] == runs["KH"]["agents"]["b"]["kind"] == "scripted_wary"
    assert runs["A0"]["agents"]["b"]["kind"] == runs["W2"]["agents"]["b"]["kind"] == "scripted"
    assert WARY_WARNING in runs["K"]["log"][0]["messages"]["b"]["text"]
    assert WARY_WARNING not in runs["A0"]["log"][0]["messages"]["b"]["text"]
    assert runs["K"]["agents"]["a"]["effort"] == "low" and runs["KH"]["agents"]["a"]["effort"] == "high"
    # The agent's own system prompt is the same with either partner: the warning arrives as a message.
    assert runs["K"]["agents"]["a"]["system"] == runs["A0"]["agents"]["a"]["system"]
    assert GOAL_STRONG in runs["A0"]["agents"]["a"]["system"] and GOAL_STRONG not in runs["W2"]["agents"]["a"]["system"]
    assert runs["HA"]["agents"]["a"]["model"] == "claude-haiku-5-5"
    w0 = json.loads((__import__("harness.backends", fromlist=["ROOT"]).ROOT / "data" / "r4" / "full" / "episodes"
                     / "r4_W0_seed0150.json").read_text())
    assert runs["A0"]["agents"]["a"]["system"] == w0["agents"]["a"]["system"]
