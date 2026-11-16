import pytest

from harness.env import PythonCoopEnv, load_params
from harness.game import Action, Message, Turn
from harness.scripted import FixedTurns, play_episode


@pytest.fixture(scope="session")
def params():
    return load_params()


@pytest.fixture
def env(params):
    return PythonCoopEnv(params)


def opening(pledge="operate"):
    return Turn(Message(pledge, ""))


def turn(kind, n=0, pledge="operate"):
    return Turn(Message(pledge, ""), Action(kind, n))


def all_wait(params):
    return FixedTurns([opening()] + [turn("wait")] * params.max_rounds)


def leak_of(env, params, seed):
    """(round, cells) of the leak in this seed's world, or None. Both agents
    operate in round 1 so the reserve is never empty when the leak comes."""
    script = [opening(), turn("operate")] + [turn("wait")] * (params.max_rounds - 1)
    truth = play_episode(env, {"a": FixedTurns(script), "b": FixedTurns(script)}, seed, "safe")
    for line in truth.lines:
        if line.get("leak"):
            return (line["round"], line["leak"])
    return None


@pytest.fixture(scope="session")
def leak_free_seed(params):
    env = PythonCoopEnv(params)
    return next(s for s in range(1000) if leak_of(env, params, s) is None)
