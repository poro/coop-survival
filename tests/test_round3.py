import json

from harness.backends import ROOT, load_models_config, load_prompts, prompt_hash
from harness.r2 import run_episode
from harness.r3 import load_r3_config, plan
from tests.test_round2 import ctx

# Round-two episode whose condition each replication cell repeats.
ROUND_TWO = {
    "R1": "r2_AB_s80_goal_strong_seed00", "R2": "r2_E_s80_goal_strong_seed00",
    "R3": "r2_D_haiku_s80_goal_strong_seed00", "R4": "r2_D_sonnet_s80_goal_strong_seed00",
    "R5": "r2_AB_s80_none_seed00", "R6": "r2_AB_s60_goal_strong_seed00",
}


def test_plan_is_six_cells_of_one_hundred_fresh_seeds():
    specs = plan(load_r3_config())
    assert len(specs) == 600 and len({s["id"] for s in specs}) == 600
    for name in ROUND_TWO:
        seeds = sorted(s["seed"] for s in specs if s["r3_cell"] == name)
        assert seeds == list(range(50, 150))
    assert {s["partner"] for s in specs} == {"cooperator"} and {s["label"] for s in specs} == {"other_ai"}
    assert len(plan(load_r3_config(), dev=True)) == 12


def test_prompt_hash_is_round_twos():
    assert prompt_hash().startswith(load_r3_config()["expected_prompt_hash"])


def test_each_cell_gets_exactly_the_round_two_prompt_model_and_settings(params):
    specs = {s["r3_cell"]: s for s in plan(load_r3_config())}
    for name, r2_id in ROUND_TWO.items():
        old = json.loads((ROOT / "data" / "r2" / "full" / "episodes" / f"{r2_id}.json").read_text())
        new = run_episode(specs[name], ctx(params))
        a_old, a_new = old["agents"]["a"], new["agents"]["a"]
        assert a_new["system"] == a_old["system"]
        assert a_new["model"] == a_old["model"] and a_new["effort"] == a_old["effort"]
        assert json.loads(json.dumps(new["game_params"])) == old["game_params"]
        assert new["agents"]["b"]["kind"] == old["agents"]["b"]["kind"] == "scripted"
