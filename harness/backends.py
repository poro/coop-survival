"""ModelClient implementations, prompt loading and the cost log.

The Anthropic backend uses the SDK's default client. It never reads an API
key itself: with no key in the environment the SDK falls back to the active
`ant` profile on disk.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
PROMPT_DIR = ROOT / "prompts"
MODELS_CONFIG = ROOT / "config" / "models.yaml"


# -- prompts -----------------------------------------------------------------


def load_prompts() -> dict[str, str]:
    return {p.stem: p.read_text(encoding="utf-8").strip("\n") for p in sorted(PROMPT_DIR.glob("*.txt"))}


def prompt_hash() -> str:
    h = hashlib.sha256()
    for path in sorted(PROMPT_DIR.glob("*.txt")):
        h.update(path.name.encode())
        h.update(b"\0")
        h.update(path.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def fill(template: str, **values) -> str:
    """Replace {{name}} placeholders in one pass. A placeholder with no value,
    or a value with no placeholder, is an error. Substituted text is never
    re-scanned, so values may contain braces."""
    names = set(re.findall(r"\{\{(\w+)\}\}", template))
    if names != set(values):
        raise KeyError(f"placeholders {sorted(names)} do not match values {sorted(values)}")
    return re.sub(r"\{\{(\w+)\}\}", lambda m: str(values[m.group(1)]), template)


def git_commit() -> str:
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--", "harness", "prompts", "config"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        ).stdout.strip()
        return head + ("-dirty" if dirty else "")
    except Exception:
        return "unknown"


# -- configuration and cost --------------------------------------------------


def load_models_config(path: Path | str = MODELS_CONFIG) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


@dataclass
class Completion:
    text: str = ""  # the text blocks, joined
    content: list = field(default_factory=list)  # blocks to send back unchanged
    content_json: list = field(default_factory=list)  # the same blocks, for the log
    stop_reason: str | None = None
    model: str | None = None
    request_id: str | None = None
    usage: dict = field(default_factory=dict)
    cost_usd: float = 0.0
    error: str | None = None  # set when the API call itself failed


class UsageMeter:
    """Token and cost totals per model. Thread-safe."""

    FIELDS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")

    def __init__(self, config: dict):
        self._prices = {m["id"]: m for m in config["models"].values()}
        self._batch_discount = config["batch_discount"]
        self._lock = threading.Lock()
        self.totals: dict[str, dict] = {}

    def cost(self, model: str, usage: dict, batch: bool = False) -> float:
        p = self._prices[model]
        dollars = (
            usage.get("input_tokens", 0) * p["input"]
            + usage.get("output_tokens", 0) * p["output"]
            + usage.get("cache_creation_input_tokens", 0) * p["cache_write"]
            + usage.get("cache_read_input_tokens", 0) * p["cache_read"]
        ) / 1_000_000
        return dollars * (self._batch_discount if batch else 1.0)

    def add(self, model: str, usage: dict, batch: bool = False) -> float:
        cost = self.cost(model, usage, batch)
        with self._lock:
            t = self.totals.setdefault(model, {"calls": 0, "cost_usd": 0.0, **{f: 0 for f in self.FIELDS}})
            t["calls"] += 1
            t["cost_usd"] += cost
            for f in self.FIELDS:
                t[f] += usage.get(f, 0) or 0
        return cost

    @property
    def total_cost(self) -> float:
        with self._lock:
            return sum(t["cost_usd"] for t in self.totals.values())

    def summary(self) -> str:
        lines = []
        for model, t in sorted(self.totals.items()):
            lines.append(
                f"  {model}: {t['calls']} calls, in {t['input_tokens']:,}, "
                f"cache write {t['cache_creation_input_tokens']:,}, cache read {t['cache_read_input_tokens']:,}, "
                f"out {t['output_tokens']:,}, ${t['cost_usd']:.4f}"
            )
        lines.append(f"  total this run: ${self.total_cost:.4f}")
        return "\n".join(lines)


# -- spend ledger ------------------------------------------------------------


def ledger_total(path: Path) -> float:
    if not path.exists():
        return 0.0
    return sum(json.loads(line)["cost_usd"] for line in path.read_text().splitlines() if line.strip())


def ledger_append(path: Path, entry: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, sort_keys=True) + "\n")


# -- clients -----------------------------------------------------------------


class ModelClient:
    """One interface for every backend."""

    backend = "abstract"

    def request_settings(self) -> dict:
        """The sampling parameters recorded with every call."""
        raise NotImplementedError

    def complete(self, *, model: str, system: str, messages: list, max_tokens: int) -> Completion:
        raise NotImplementedError


def _usage_dict(usage) -> dict:
    return {f: getattr(usage, f, 0) or 0 for f in UsageMeter.FIELDS}


class AnthropicClient(ModelClient):
    backend = "anthropic"

    def __init__(self, config: dict, meter: UsageMeter):
        import anthropic

        self._anthropic = anthropic
        # The SDK's default client: credentials come from the SDK's own chain.
        self._client = anthropic.Anthropic()
        self._request = config["request"]
        self.meter = meter

    def request_settings(self) -> dict:
        return {
            "temperature": "not accepted by the API for these models",
            "effort": self._request["effort"],
            "thinking": self._request["thinking"],
            "sampling_seed": "not offered by the API",
        }

    def params(self, *, model: str, system: str, messages: list, max_tokens: int) -> dict:
        return {
            "model": model,
            "max_tokens": max_tokens,
            # Two cache breakpoints: the system prompt (shared by every episode
            # in a condition) and, through top-level cache_control, the end of
            # the conversation so far.
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "cache_control": {"type": "ephemeral"},
            "messages": messages,
            "thinking": dict(self._request["thinking"]),
            "output_config": {"effort": self._request["effort"]},
        }

    def _to_completion(self, message, batch: bool = False) -> Completion:
        usage = _usage_dict(message.usage)
        return Completion(
            text="".join(b.text for b in message.content if b.type == "text"),
            content=list(message.content),
            content_json=[b.to_dict() for b in message.content],
            stop_reason=message.stop_reason,
            model=message.model,
            request_id=getattr(message, "_request_id", None),
            usage=usage,
            cost_usd=self.meter.add(message.model, usage, batch=batch),
        )

    def complete(self, *, model: str, system: str, messages: list, max_tokens: int) -> Completion:
        a = self._anthropic
        try:
            message = self._client.with_options(max_retries=4, timeout=180.0).messages.create(
                **self.params(model=model, system=system, messages=messages, max_tokens=max_tokens)
            )
        except a.BadRequestError:
            raise  # our request is wrong: fail loudly, do not record as data
        except (a.AuthenticationError, a.PermissionDeniedError, a.NotFoundError):
            raise
        except a.RateLimitError as e:
            return Completion(error=f"rate_limit: {e.message}")
        except a.APITimeoutError:
            return Completion(error="timeout")
        except a.APIConnectionError as e:
            return Completion(error=f"connection: {e}")
        except a.APIStatusError as e:
            return Completion(error=f"api_status_{e.status_code}: {e.message}")
        return self._to_completion(message)

    # -- Message Batches (stage 4) -----------------------------------------

    def batch_submit(self, requests: list[dict]) -> str:
        """requests: [{"custom_id": ..., "params": {...}}]. Returns the batch id."""
        from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
        from anthropic.types.messages.batch_create_params import Request

        batch = self._client.messages.batches.create(
            requests=[
                Request(custom_id=r["custom_id"], params=MessageCreateParamsNonStreaming(**r["params"]))
                for r in requests
            ]
        )
        return batch.id

    def batch_status(self, batch_id: str):
        return self._client.messages.batches.retrieve(batch_id)

    def batch_results(self, batch_id: str) -> dict[str, Completion]:
        out = {}
        for item in self._client.messages.batches.results(batch_id):
            if item.result.type == "succeeded":
                out[item.custom_id] = self._to_completion(item.result.message, batch=True)
            else:
                out[item.custom_id] = Completion(error=f"batch_{item.result.type}")
        return out


class ScriptedClient(ModelClient):
    """Test backend: answers from a function of (system, messages). No network."""

    backend = "scripted"

    def __init__(self, respond):
        self._respond = respond
        self.calls: list[dict] = []

    def request_settings(self) -> dict:
        return {"scripted": True}

    def complete(self, *, model: str, system: str, messages: list, max_tokens: int) -> Completion:
        self.calls.append({"model": model, "system": system, "messages": json.loads(json.dumps(messages))})
        reply = self._respond(system, messages)
        if isinstance(reply, Completion):
            return reply
        block = {"type": "text", "text": reply}
        return Completion(text=reply, content=[block], content_json=[block], stop_reason="end_turn", model=model)
