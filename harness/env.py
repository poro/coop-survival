"""The CoopEnv interface and its pure-Python implementation.

All harness code talks to CoopEnv. A game engine backend added later only has
to implement these four methods with the same types and the same log schema.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

import yaml

from harness.game import Game, GameParams, GroundTruth, Observation, PartialLog, Turn

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config" / "experiment.yaml"


class CoopEnv(ABC):
    @abstractmethod
    def reset(self, seed: int, pressure: str) -> dict[str, Observation]: ...

    @abstractmethod
    def step(self, turns: dict[str, Turn]) -> dict[str, Observation]:
        """Both agents act at once. The first step after reset carries the
        opening messages only (Turn.action is None)."""

    @abstractmethod
    def truth(self) -> GroundTruth:
        """The full log. Never shown to agents."""

    @abstractmethod
    def partner_view(self, agent_id: str) -> PartialLog:
        """What that agent's partner could see."""


def load_config(path: Path | str = DEFAULT_CONFIG) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_params(path: Path | str = DEFAULT_CONFIG) -> GameParams:
    game = dict(load_config(path)["game"])
    game["leak_sizes"] = tuple(game["leak_sizes"])
    return GameParams(**game)


class PythonCoopEnv(CoopEnv):
    def __init__(self, params: GameParams | None = None):
        self.params = params or load_params()
        self._game: Game | None = None

    def _require_game(self) -> Game:
        if self._game is None:
            raise RuntimeError("call reset() first")
        return self._game

    def reset(self, seed: int, pressure: str) -> dict[str, Observation]:
        self._game = Game(self.params, seed, pressure)
        return self._game.observations()

    def step(self, turns: dict[str, Turn]) -> dict[str, Observation]:
        return self._require_game().step(turns)

    def truth(self) -> GroundTruth:
        return self._require_game().truth()

    def partner_view(self, agent_id: str) -> PartialLog:
        return self._require_game().partner_view(agent_id)
