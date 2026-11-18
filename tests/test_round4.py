import json

from harness.backends import ROOT, prompt_hash
from harness.consistency import verdict
from harness.game import GameParams
from harness.overseer import NO_REPORT, render_case, render_overseer_system
from harness.r2 import run_episode
from harness.r4 import load_cases, load_r4_config, load_r4_prompts, plan, r4_prompt_hash
from tests.test_round2 import GOAL_STRONG, ctx

R3_EPISODES = ROOT / "data" / "r3" / "full" / "episodes"


def r4_ctx(params):
    c = ctx(params)
    c["prompts"] = load_r4_prompts()
    return c


def episode(name):
    return json.loads((R3_EPISODES / f"{name}.json").read_text())


def test_plan_is_1700_episodes_on_fresh_seeds():
    specs = plan(load_r4_config())
    assert len(specs) == 1700 and len({s["id"] for s in specs}) == 1700
    by_cell = {}
    for s in specs:
        by_cell.setdefault(s["r4_cell"], []).append(s["seed"])
    assert sorted(by_cell["H"]) == list(range(150, 1150))
    for name in ("P65", "P70", "P75", "W0", "W1", "W2", "L"):
        assert sorted(by_cell[name]) == list(range(150, 250))
    assert min(s["seed"] for s in specs) == 150  # rounds one to three used seeds 0 to 149
    assert len(plan(load_r4_config(), dev=True)) == 16


def test_round_two_prompts_are_untouched_and_round_four_adds_three():
    assert prompt_hash().startswith(load_r4_config()["expected_r2_prompt_hash"])
    added = set(load_r4_prompts()) - set(__import__("harness.backends", fromlist=["load_prompts"]).load_prompts())
    assert added == {"objective_goal_strong_p1", "objective_goal_strong_p2", "overseer_system_no_report"}
    assert not r4_prompt_hash().startswith(prompt_hash()[:12])


def test_w0_is_exactly_the_replication_primary_cell(params):
    w0 = next(s for s in plan(load_r4_config()) if s["r4_cell"] == "W0")
    new = run_episode(w0, r4_ctx(params))
    old = episode("r3_R1_seed050")
    assert new["agents"]["a"]["system"] == old["agents"]["a"]["system"]
    assert new["agents"]["a"]["model"] == old["agents"]["a"]["model"] and new["agents"]["a"]["effort"] == "low"
    assert json.loads(json.dumps(new["game_params"])) == old["game_params"]


def test_each_cell_changes_only_what_the_prd_says(params):
    specs = {s["r4_cell"]: s for s in plan(load_r4_config(), dev=True)}
    runs = {name: run_episode(spec, r4_ctx(params)) for name, spec in specs.items()}
    w0 = runs["W0"]["agents"]["a"]["system"]
    assert GOAL_STRONG in w0
    for name in ("W1", "W2"):
        system = runs[name]["agents"]["a"]["system"]
        assert GOAL_STRONG not in system and "MISSION CONTROL OBJECTIVE" in system
        assert system.split("MISSION CONTROL OBJECTIVE")[0] == w0.split("MISSION CONTROL OBJECTIVE")[0]
    for name, storm in (("P65", 0.65), ("P70", 0.70), ("P75", 0.75)):
        assert runs[name]["game_params"]["storm_probability"] == storm
        assert runs[name]["agents"]["a"]["system"].replace(f"{round(storm * 100)}%", "80%") == w0
    assert runs["H"]["agents"]["a"]["model"] == "claude-haiku-5-5"
    live = runs["L"]["agents"]
    assert live["a"]["kind"] == live["b"]["kind"] == "model" and live["a"]["label"] == "same_model"
    assert all(r["prompt_hash"] == "test" for r in runs.values())


def test_no_report_case_shows_the_full_view_and_no_report():
    ep = episode("r3_R1_seed050")
    case = json.loads(render_case(None, ep["partner_views"]["a"], NO_REPORT))
    assert "report" not in case
    assert "partner_action" in case["partner_view"]["rounds"][1]
    assert render_case(ep["agents"]["a"]["report"], ep["partner_views"]["a"], "full_view").startswith('{"report":')


def test_no_report_overseer_prompt_differs_only_in_what_it_is_given():
    prompts = load_r4_prompts()
    gp = dict(episode("r3_R1_seed050")["game_params"]); gp["leak_sizes"] = tuple(gp["leak_sizes"])
    new = render_overseer_system(prompts, GameParams(**gp), "risk", NO_REPORT)
    old = render_overseer_system(prompts, GameParams(**gp), "risk", "full_view")
    assert "report to mission control" in old and "report to mission control" not in new
    assert new.split("WHAT YOU ARE GIVEN")[0] == old.split("WHAT YOU ARE GIVEN")[0]
    assert new.split("HOW TO REPLY")[1] == old.split("HOW TO REPLY")[1]


def test_checker_without_a_report():
    def check(name):
        ep = episode(name)
        gp = dict(ep["game_params"]); gp["leak_sizes"] = tuple(gp["leak_sizes"])
        return verdict(GameParams(**gp), None, ep["partner_views"]["a"], NO_REPORT)
    # Took all 4 cells in round 1 while the partner operated: no agreement-keeping reading.
    assert check("r3_R1_seed050") == "decidable"
    kept = next(p.stem for p in sorted(R3_EPISODES.glob("r3_R5_*.json"))
                if not p.stem.endswith(".log") and json.loads(p.read_text())["agents"]["a"]["labels"]["broke_agreement"] is False)
    assert check(kept) == "ambiguous"


def test_overseer_cases_cover_model_reports_in_the_overseen_cells(tmp_path, params):
    out = tmp_path / "episodes"; out.mkdir()
    for spec in plan(load_r4_config(), dev=True):
        rec = run_episode(spec, r4_ctx(params))
        (out / f"{spec['id']}.json").write_text(json.dumps(rec))
    cases = load_cases(out, ["H", "W0"])
    assert {c["r4_cell"] for c in cases} == {"H", "W0"} and len(cases) == 4
    assert all(c["evidence"] == NO_REPORT and c["consistency"] in ("decidable", "ambiguous") for c in cases)
