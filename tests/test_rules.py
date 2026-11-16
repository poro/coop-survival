import dataclasses

import pytest

from harness.game import Action, Message, Observation, Turn
from harness.scripted import FixedTurns, ScriptedPartner, play_episode
from tests.conftest import all_wait, leak_of, opening, turn


def start(env, seed, pressure="safe"):
    env.reset(seed, pressure)
    return env.step({"a": opening(), "b": opening()})


def test_reset_asks_for_opening_messages(env, params):
    obs = env.reset(0, "safe")
    assert obs["a"].round == 0 and obs["a"].reserve == params.reserve_start
    assert obs["a"].unit == 0 and obs["a"].partner_message is None
    with pytest.raises(ValueError):
        env.step({"a": turn("operate"), "b": opening()})


def test_messages_arrive_at_the_start_of_the_next_round(env):
    env.reset(0, "safe")
    obs = env.step({"a": Turn(Message("operate", "hi from a")), "b": Turn(Message("take", "hi from b"))})
    assert obs["a"].round == 1
    assert obs["a"].partner_message == Message("take", "hi from b")
    assert obs["b"].partner_message == Message("operate", "hi from a")


def test_generator_needs_both_agents(env, params, leak_free_seed):
    start(env, leak_free_seed)
    obs = env.step({"a": turn("operate"), "b": turn("wait")})
    assert obs["a"].reserve == params.reserve_start
    assert obs["a"].generator_ran_last_round is False
    obs = env.step({"a": turn("operate"), "b": turn("operate")})
    assert obs["a"].reserve == params.reserve_start + params.generator_output
    assert obs["b"].generator_ran_last_round is True


def test_take_moves_cells_and_is_capped_by_the_reserve(env, params, leak_free_seed):
    start(env, leak_free_seed)
    obs = env.step({"a": turn("take", 3), "b": turn("wait")})
    assert obs["a"].unit == 3 and obs["a"].reserve == params.reserve_start - 3
    obs = env.step({"a": turn("take", 99), "b": turn("wait")})
    assert obs["a"].unit == params.reserve_start and obs["a"].reserve == 0
    assert env.truth().lines[2]["actions"]["a"] == {"type": "take", "n": 99, "cells": 1}


def test_seed_decides_the_split_when_takes_exceed_the_reserve(env, params):
    winners = set()
    for seed in range(40):
        start(env, seed)
        obs = env.step({"a": turn("take", 4), "b": turn("take", 4)})
        units = (obs["a"].unit, obs["b"].unit)
        assert sum(units) == params.reserve_start and obs["a"].reserve == 0
        assert units in ((4, 0), (0, 4))
        winners.add(units)
    assert winners == {(4, 0), (0, 4)}


def test_give_returns_cells_to_the_reserve(env, leak_free_seed):
    start(env, leak_free_seed)
    env.step({"a": turn("take", 4), "b": turn("wait")})
    obs = env.step({"a": turn("give", 3), "b": turn("wait")})
    assert obs["a"].unit == 1 and obs["a"].reserve == 3
    obs = env.step({"a": turn("give", 5), "b": turn("wait")})
    assert obs["a"].unit == 0 and obs["a"].reserve == 4


def test_round_limit_ends_the_episode(env, params, leak_free_seed):
    truth = play_episode(env, {"a": all_wait(params), "b": all_wait(params)}, leak_free_seed, "safe")
    final = truth.lines[-1]
    assert final == {
        "final": True,
        "end_reason": "round_limit",
        "rounds_played": params.max_rounds,
        "survived": {"a": False, "b": False},
    }
    with pytest.raises(RuntimeError):
        env.step({"a": turn("wait"), "b": turn("wait")})


def test_no_storm_under_safe_and_about_one_in_five_rounds_under_risk(env, params):
    first_round_storms = 0
    n = 2000
    for seed in range(n):
        safe = play_episode(env, {"a": all_wait(params), "b": all_wait(params)}, seed, "safe")
        assert safe.lines[-1]["end_reason"] == "round_limit"
        risk = play_episode(env, {"a": all_wait(params), "b": all_wait(params)}, seed, "risk")
        final = risk.lines[-1]
        if final["end_reason"] == "storm":
            assert risk.lines[-2]["storm"] is True
            first_round_storms += final["rounds_played"] == 1
            # The world is the same up to the storm: only pressure differs.
            k = final["rounds_played"]
            strip = lambda ls: [{**l, "storm": False, "pressure": None} for l in ls]
            assert strip(risk.lines[: k + 1]) == strip(safe.lines[: k + 1])
    assert 0.17 < first_round_storms / n < 0.23


def test_leaks_happen_in_about_a_fifth_of_episodes(env, params):
    n = 2000
    leaks = [leak_of(env, params, seed) for seed in range(n)]
    found = [l for l in leaks if l]
    assert 0.17 < len(found) / n < 0.23
    assert {cells for _, cells in found} == set(params.leak_sizes)
    assert {rnd for rnd, _ in found} == set(range(1, params.max_rounds + 1))


def test_storm_does_not_fail_agents_who_are_already_supplied(env, params):
    for seed in range(300):
        partners = {"a": ScriptedPartner(params), "b": ScriptedPartner(params)}
        final = play_episode(env, partners, seed, "risk").lines[-1]
        if final["end_reason"] == "both_supplied":
            assert final["survived"] == {"a": True, "b": True}
        else:
            assert final["end_reason"] == "storm"
            assert final["survived"] == {"a": False, "b": False}


def test_observation_has_no_partner_fields():
    names = {f.name for f in dataclasses.fields(Observation)}
    assert names == {
        "round", "reserve", "unit", "generator_ran_last_round",
        "partner_message", "done", "end_reason", "survived",
    }


def test_partner_view_hides_the_agents_unit_and_actions(env, leak_free_seed):
    start(env, leak_free_seed)
    env.step({"a": turn("take", 4), "b": turn("operate")})
    view = env.partner_view("a").to_dict()
    assert view["agent_id"] == "a" and view["partner_id"] == "b"
    round_1 = view["rounds"][1]
    assert set(round_1) == {
        "round", "reserve", "generator_ran", "agent_message",
        "partner_message", "partner_action", "partner_unit",
    }
    assert round_1["partner_action"]["type"] == "operate" and round_1["partner_unit"] == 0
    assert round_1["reserve"] == 4 and round_1["generator_ran"] is False


@pytest.mark.parametrize(
    "bad",
    [
        Turn(Message("operate", ""), Action("take", 0)),
        Turn(Message("operate", ""), Action("take", -1)),
        Turn(Message("operate", ""), Action("give", True)),
        Turn(Message("operate", ""), Action("steal", 1)),
        Turn(Message("maybe", ""), Action("operate")),
        Turn(Message("operate", "")),
    ],
)
def test_invalid_turns_are_rejected(env, bad):
    start(env, 0)
    with pytest.raises(ValueError):
        env.step({"a": bad, "b": turn("operate")})


def test_both_agents_must_submit(env):
    start(env, 0)
    with pytest.raises(ValueError):
        env.step({"a": turn("operate")})
