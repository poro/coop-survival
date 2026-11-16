import os
import subprocess
import sys
from pathlib import Path

from harness.scripted import EarlyTaker, ScriptedPartner, play_episode

ROOT = Path(__file__).resolve().parent.parent

SCRIPT = """
import sys
from harness.env import PythonCoopEnv
from harness.scripted import EarlyTaker, ScriptedPartner, play_episode
env = PythonCoopEnv()
out = []
for seed in range(25):
    for pressure in ("safe", "risk"):
        out.append(play_episode(env, {"a": ScriptedPartner(env.params), "b": ScriptedPartner(env.params)}, seed, pressure).to_jsonl())
        out.append(play_episode(env, {"a": EarlyTaker(), "b": ScriptedPartner(env.params)}, seed, pressure).to_jsonl())
sys.stdout.buffer.write("".join(out).encode("utf-8"))
"""


def run_in_fresh_process(hash_seed: str) -> bytes:
    env = {**os.environ, "PYTHONHASHSEED": hash_seed, "PYTHONPATH": str(ROOT)}
    done = subprocess.run([sys.executable, "-c", SCRIPT], cwd=ROOT, env=env, capture_output=True, check=True)
    return done.stdout


def test_same_seed_and_turns_give_byte_identical_logs_in_separate_runs():
    first, second = run_in_fresh_process("1"), run_in_fresh_process("987654")
    assert len(first) > 10_000
    assert first == second


def test_same_seed_and_turns_give_byte_identical_logs_in_one_process(env, params):
    for seed in range(25):
        logs = {
            play_episode(env, {"a": EarlyTaker(), "b": ScriptedPartner(params)}, seed, "risk")
            .to_jsonl()
            .encode("utf-8")
            for _ in range(3)
        }
        assert len(logs) == 1


def test_different_seeds_give_different_worlds(env, params):
    logs = {
        play_episode(env, {"a": ScriptedPartner(params), "b": ScriptedPartner(params)}, seed, "risk").to_jsonl()
        for seed in range(50)
    }
    assert len(logs) == 50
