"""The model-driven agent: one call per round, then one report call.

The conversation is append-only. Each assistant reply is sent back exactly as
it came, so the earlier rounds form a stable, cacheable prefix.
"""

from __future__ import annotations

import json

from harness.backends import Completion, ModelClient, fill
from harness.game import Action, GameParams, Message, Observation, Turn, validate_turn

REPORT_PARTNER_ANSWERS = ("yes", "no", "unsure")


class InvalidEpisode(Exception):
    """A refusal, an API failure, or two unusable replies in a row."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


# -- prompts -----------------------------------------------------------------


def render_rules(prompts: dict, params: GameParams, pressure: str) -> str:
    pressure_text = prompts[f"pressure_{pressure}"]
    if pressure == "risk":
        pressure_text = fill(
            pressure_text,
            storm_percent=round(params.storm_probability * 100),
            cells_needed=params.cells_needed,
        )
    return fill(
        prompts["rules"],
        cells_needed=params.cells_needed,
        reserve_start=params.reserve_start,
        max_rounds=params.max_rounds,
        generator_output=params.generator_output,
        leak_sizes=" or ".join(str(n) for n in params.leak_sizes),
        pressure=pressure_text,
        reserve_target=params.reserve_target,
        share=params.share,
    )


def render_agent_system(
    prompts: dict, params: GameParams, pressure: str, label: str, control: bool = False
) -> str:
    control_text = ""
    if control:
        control_text = "\n\n" + fill(prompts["control"], reserve_start=params.reserve_start)
    return fill(
        prompts["agent_system"],
        label=prompts[f"label_{label}"],
        rules=render_rules(prompts, params, pressure),
        control=control_text,
    )


def render_observation(obs: Observation) -> str:
    if obs.round == 0:
        data = {"round": 0, "opening": True, "reserve": obs.reserve, "your_unit": obs.unit}
    else:
        pm = obs.partner_message
        data = {
            "round": obs.round,
            "reserve": obs.reserve,
            "your_unit": obs.unit,
            "generator_ran_last_round": obs.generator_ran_last_round,
            "partner_message": {"next_action": pm.next_action, "text": pm.text} if pm else None,
        }
    return json.dumps(data, separators=(",", ":"))


def render_report_request(prompts: dict, obs: Observation) -> str:
    outcome = {
        "episode_over": True,
        "end_reason": obs.end_reason,
        "rounds_played": obs.round,
        "reserve": obs.reserve,
        "your_unit": obs.unit,
        "you_survived": obs.survived,
    }
    return fill(prompts["report_request"], outcome=json.dumps(outcome, separators=(",", ":")))


# -- parsing -----------------------------------------------------------------


def parse_json_object(text: str) -> dict:
    """The one JSON object in a reply. Code fences and stray text around it
    are tolerated; anything else is an error."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object found in the reply")
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError as e:
        raise ValueError(f"invalid JSON: {e}") from None
    if not isinstance(data, dict):
        raise ValueError("the reply must be a JSON object")
    return data


def _parse_message(data: dict) -> Message:
    message = data.get("message")
    if not isinstance(message, dict):
        raise ValueError('"message" must be an object with next_action and text')
    text = message.get("text", "")
    if not isinstance(text, str):
        raise ValueError('"message.text" must be a string')
    return Message(next_action=message.get("next_action"), text=text)


def parse_turn(text: str, opening: bool) -> Turn:
    data = parse_json_object(text)
    message = _parse_message(data)
    if opening:
        if data.get("action") is not None:
            raise ValueError("the opening reply carries a message only, with no action")
        turn = Turn(message)
    else:
        action = data.get("action")
        if not isinstance(action, dict):
            raise ValueError('"action" must be an object with a type')
        kind = action.get("type")
        n = action.get("n", 0) if kind in ("take", "give") else 0
        turn = Turn(message, Action(type=kind, n=n))
    validate_turn(turn, opening)
    return turn


def parse_report(text: str) -> dict:
    data = parse_json_object(text)
    if not isinstance(data.get("followed_agreement"), bool):
        raise ValueError('"followed_agreement" must be true or false')
    for key in ("cells_taken_before_reserve_reached_8", "total_cells_taken"):
        value = data.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f'"{key}" must be a whole number, 0 or more')
    if data.get("partner_followed_agreement") not in REPORT_PARTNER_ANSWERS:
        raise ValueError(f'"partner_followed_agreement" must be one of {REPORT_PARTNER_ANSWERS}')
    if not isinstance(data.get("account"), str):
        raise ValueError('"account" must be a string')
    return {
        "followed_agreement": data["followed_agreement"],
        "cells_taken_before_reserve_reached_8": data["cells_taken_before_reserve_reached_8"],
        "total_cells_taken": data["total_cells_taken"],
        "partner_followed_agreement": data["partner_followed_agreement"],
        "account": data["account"],
    }


# -- the agent ---------------------------------------------------------------


class ModelAgent:
    def __init__(self, client: ModelClient, model: str, system: str, prompts: dict, max_tokens: dict):
        self.client = client
        self.model = model
        self.system = system
        self.prompts = prompts
        self.max_tokens = max_tokens
        self._messages: list = []  # sent to the API; assistant blocks unchanged
        self.conversation: list[dict] = []  # the same conversation, for the log
        self.calls: list[dict] = []
        self.retries = 0

    def reset(self, seed: int) -> None:
        pass

    def _call(self, purpose: str) -> Completion:
        done = self.client.complete(
            model=self.model, system=self.system, messages=self._messages,
            max_tokens=self.max_tokens[purpose if purpose in self.max_tokens else "turn"],
        )
        self.calls.append(
            {
                "purpose": purpose,
                "prompt_messages": len(self._messages),  # the prompt is system + conversation[:n]
                "model": done.model,
                "request_id": done.request_id,
                "stop_reason": done.stop_reason,
                "usage": done.usage,
                "cost_usd": done.cost_usd,
                "error": done.error,
                "completion": done.content_json,
            }
        )
        if done.error:
            raise InvalidEpisode("api_error", done.error)
        if done.stop_reason == "refusal":
            raise InvalidEpisode("refusal")
        if done.content:
            self._messages.append({"role": "assistant", "content": done.content})
            self.conversation.append({"role": "assistant", "content": done.content_json})
        return done

    def _say(self, text: str) -> None:
        self._messages.append({"role": "user", "content": text})
        self.conversation.append({"role": "user", "content": text})

    def _ask(self, text: str, parse, purpose: str):
        self._say(text)
        done = self._call(purpose)
        try:
            return parse(done.text)
        except ValueError as first:
            self.retries += 1
            self._say(fill(self.prompts["retry"], error=str(first)))
            done = self._call(purpose)
            try:
                return parse(done.text)
            except ValueError as second:
                raise InvalidEpisode("parse_failure", str(second)) from None

    def turn(self, obs: Observation) -> Turn:
        opening = obs.round == 0
        return self._ask(render_observation(obs), lambda t: parse_turn(t, opening), "turn")

    def report(self, final_obs: Observation) -> dict:
        return self._ask(render_report_request(self.prompts, final_obs), parse_report, "report")
