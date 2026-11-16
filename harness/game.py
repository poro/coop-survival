"""The outpost rules.

Pure Python, standard library only: no model calls, no network access, no
third-party imports. Every number comes in through GameParams, which env.py
fills from config/experiment.yaml.

Order of events inside one round:
  1. takes   (if the two takes exceed the reserve, the seed decides who goes first)
  2. gives   (cells move from the giver's own unit back to the shared reserve)
  3. generator (adds cells only if both agents chose operate)
  4. leak    (only in the seeded leak round of a leak episode)
  5. end check: both units supplied, else storm, else round limit

All randomness is drawn once, at construction, from one generator seeded with
the episode seed. World events therefore never depend on what the agents do,
and the same seed gives the same leak and storm schedule in every condition.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass

AGENTS = ("a", "b")
PRESSURES = ("safe", "risk")
ACTION_TYPES = ("operate", "take", "give", "wait")
PLEDGES = ("operate", "take", "undecided")


def other(agent_id: str) -> str:
    if agent_id not in AGENTS:
        raise ValueError(f"unknown agent {agent_id!r}")
    return AGENTS[1] if agent_id == AGENTS[0] else AGENTS[0]


@dataclass(frozen=True)
class GameParams:
    max_rounds: int
    cells_needed: int  # cells a unit needs for its agent to survive
    reserve_start: int
    reserve_target: int  # the agreement: no taking before the reserve holds this
    share: int  # the agreement: each agent takes at most this many in total
    generator_output: int
    storm_probability: float  # per round, risk pressure only
    leak_probability: float  # per episode
    leak_sizes: tuple[int, ...]


@dataclass(frozen=True)
class Action:
    type: str
    n: int = 0  # cells requested, for take and give


@dataclass(frozen=True)
class Message:
    next_action: str
    text: str = ""


@dataclass(frozen=True)
class Turn:
    message: Message
    action: Action | None = None  # None only for the opening message


@dataclass(frozen=True)
class Observation:
    round: int  # the round about to be played; 0 means "send your opening message"
    reserve: int
    unit: int  # the agent's own unit; the partner's is never shown
    generator_ran_last_round: bool | None
    partner_message: Message | None
    done: bool = False
    end_reason: str | None = None
    survived: bool | None = None  # the agent's own outcome, once done


@dataclass(frozen=True)
class GroundTruth:
    """The full episode log. Never shown to agents or overseers."""

    lines: tuple[dict, ...]

    def to_jsonl(self) -> str:
        return "".join(
            json.dumps(line, sort_keys=True, separators=(",", ":")) + "\n"
            for line in self.lines
        )


@dataclass(frozen=True)
class PartialLog:
    """What the partner of `agent_id` could see: the reserve at the start of
    each round, whether the generator ran, both agents' messages, the partner's
    own actions and unit, and the outcome. Never `agent_id`'s unit or actions."""

    agent_id: str
    partner_id: str
    rounds: tuple[dict, ...]
    final_reserve: int | None
    end_reason: str | None
    survived: dict | None  # {"agent": bool, "partner": bool}

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "partner_id": self.partner_id,
            "rounds": list(self.rounds),
            "final_reserve": self.final_reserve,
            "end_reason": self.end_reason,
            "survived": self.survived,
        }


def _validate_turn(turn: Turn, opening: bool) -> None:
    if not isinstance(turn, Turn) or not isinstance(turn.message, Message):
        raise ValueError("a turn needs a Message")
    if turn.message.next_action not in PLEDGES:
        raise ValueError(f"next_action must be one of {PLEDGES}")
    if not isinstance(turn.message.text, str):
        raise ValueError("message text must be a string")
    if opening:
        if turn.action is not None:
            raise ValueError("the opening turn carries a message only")
        return
    action = turn.action
    if not isinstance(action, Action) or action.type not in ACTION_TYPES:
        raise ValueError(f"action type must be one of {ACTION_TYPES}")
    if action.type in ("take", "give"):
        if isinstance(action.n, bool) or not isinstance(action.n, int) or action.n < 1:
            raise ValueError(f"{action.type} needs a whole number of cells, 1 or more")


class Game:
    def __init__(self, params: GameParams, seed: int, pressure: str):
        if pressure not in PRESSURES:
            raise ValueError(f"pressure must be one of {PRESSURES}")
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise ValueError("seed must be an int")
        self.params = params
        self.seed = seed
        self.pressure = pressure

        # Only rng.random() is used: its sequence for a given int seed is the
        # one part of the random module guaranteed stable across versions.
        rng = random.Random(seed)
        is_leak_episode = rng.random() < params.leak_probability
        leak_round = 1 + int(rng.random() * params.max_rounds)
        leak_size = params.leak_sizes[int(rng.random() * len(params.leak_sizes))]
        storm_rolls = [rng.random() for _ in range(params.max_rounds)]
        first_rolls = [rng.random() for _ in range(params.max_rounds)]

        self._leak = (leak_round, leak_size) if is_leak_episode else None
        self._storm_round = None
        if pressure == "risk":
            for i, roll in enumerate(storm_rolls):
                if roll < params.storm_probability:
                    self._storm_round = i + 1
                    break
        self._takes_first = [AGENTS[0] if x < 0.5 else AGENTS[1] for x in first_rolls]

        self.round = 0  # 0 = waiting for opening messages
        self.reserve = params.reserve_start
        self.units = {a: 0 for a in AGENTS}
        self.done = False
        self.end_reason: str | None = None
        self._generator_ran: bool | None = None
        self._last_messages: dict[str, Message] | None = None
        self._lines: list[dict] = []

    # -- what agents see ---------------------------------------------------

    def observe(self, agent_id: str) -> Observation:
        partner = other(agent_id)
        return Observation(
            round=self.round,
            reserve=self.reserve,
            unit=self.units[agent_id],
            generator_ran_last_round=self._generator_ran,
            partner_message=self._last_messages[partner] if self._last_messages else None,
            done=self.done,
            end_reason=self.end_reason,
            survived=self._survived(agent_id) if self.done else None,
        )

    def observations(self) -> dict[str, Observation]:
        return {a: self.observe(a) for a in AGENTS}

    def _survived(self, agent_id: str) -> bool:
        return self.units[agent_id] >= self.params.cells_needed

    # -- one step ----------------------------------------------------------

    def step(self, turns: dict[str, Turn]) -> dict[str, Observation]:
        if self.done:
            raise RuntimeError("the episode is over")
        if set(turns) != set(AGENTS):
            raise ValueError(f"turns are needed for exactly {AGENTS}")
        opening = self.round == 0
        for agent_id in AGENTS:
            _validate_turn(turns[agent_id], opening)

        messages = {
            a: {"next_action": turns[a].message.next_action, "text": turns[a].message.text}
            for a in AGENTS
        }
        self._last_messages = {a: turns[a].message for a in AGENTS}

        if opening:
            p = self.params
            self._lines.append(
                {
                    "round": 0,
                    "seed": self.seed,
                    "pressure": self.pressure,
                    "agreement": {"reserve_target": p.reserve_target, "share": p.share},
                    "cells_needed": p.cells_needed,
                    "messages": messages,
                    "reserve": self.reserve,
                    "units": dict(self.units),
                }
            )
            self.round = 1
            return self.observations()

        r = self.round
        reserve_start = self.reserve
        moved = {a: 0 for a in AGENTS}

        first = self._takes_first[r - 1]
        for agent_id in (first, other(first)):
            action = turns[agent_id].action
            if action.type == "take":
                got = min(action.n, self.reserve)
                self.reserve -= got
                self.units[agent_id] += got
                moved[agent_id] = got

        for agent_id in AGENTS:
            action = turns[agent_id].action
            if action.type == "give":
                given = min(action.n, self.units[agent_id])
                self.units[agent_id] -= given
                self.reserve += given
                moved[agent_id] = given

        generator_ran = all(turns[a].action.type == "operate" for a in AGENTS)
        if generator_ran:
            self.reserve += self.params.generator_output

        leaked = 0
        if self._leak and self._leak[0] == r:
            leaked = min(self._leak[1], self.reserve)
            self.reserve -= leaked

        both_supplied = all(self._survived(a) for a in AGENTS)
        storm = not both_supplied and self._storm_round == r

        self._lines.append(
            {
                "round": r,
                "reserve_start": reserve_start,
                "actions": {
                    a: {
                        "type": turns[a].action.type,
                        "n": turns[a].action.n if turns[a].action.type in ("take", "give") else 0,
                        "cells": moved[a],
                    }
                    for a in AGENTS
                },
                "messages": messages,
                "generator_ran": generator_ran,
                "leak": leaked,
                "storm": storm,
                "reserve": self.reserve,
                "units": dict(self.units),
            }
        )
        self._generator_ran = generator_ran

        if both_supplied:
            self.end_reason = "both_supplied"
        elif storm:
            self.end_reason = "storm"
        elif r >= self.params.max_rounds:
            self.end_reason = "round_limit"

        if self.end_reason:
            self.done = True
            self._lines.append(
                {
                    "final": True,
                    "end_reason": self.end_reason,
                    "rounds_played": r,
                    "survived": {a: self._survived(a) for a in AGENTS},
                }
            )
        else:
            self.round = r + 1
        return self.observations()

    # -- logs --------------------------------------------------------------

    def truth(self) -> GroundTruth:
        return GroundTruth(lines=tuple(json.loads(json.dumps(line)) for line in self._lines))

    def partner_view(self, agent_id: str) -> PartialLog:
        partner = other(agent_id)
        rounds = []
        for line in self._lines:
            if line.get("final"):
                continue
            entry = {
                "round": line["round"],
                "agent_message": line["messages"][agent_id],
                "partner_message": line["messages"][partner],
            }
            if line["round"] == 0:
                entry["reserve"] = line["reserve"]
            else:
                entry["reserve"] = line["reserve_start"]
                entry["generator_ran"] = line["generator_ran"]
                entry["partner_action"] = line["actions"][partner]
                entry["partner_unit"] = line["units"][partner]
            rounds.append(json.loads(json.dumps(entry)))
        return PartialLog(
            agent_id=agent_id,
            partner_id=partner,
            rounds=tuple(rounds),
            final_reserve=self.reserve if self.done else None,
            end_reason=self.end_reason,
            survived=(
                {"agent": self._survived(agent_id), "partner": self._survived(partner)}
                if self.done
                else None
            ),
        )
