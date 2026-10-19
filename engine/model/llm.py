"""Asking the model for JSON, with the per-design LLM-failure budget.

An LLM failure is a reply that can't be used at all. It is retried with
the mistake quoted back, and counts against its own budget (3 in a row or
6 in total per design), never against the design-attempt cap. An
unreachable model ends the budget at once, since retrying can't help.
"""

import json
import re
import time
from pathlib import Path
from string import Template
from typing import Any, Callable, Optional, TypeVar

from engine.model.model import ImagesUnsupported, ModelAdapter, ModelUnavailable
from engine.tracing.trace import record

T = TypeVar("T")

MAX_CONSECUTIVE_FAILURES = 3
MAX_TOTAL_FAILURES = 6


class UnusableReply(Exception):
    """The model's reply can't be used; the message is quoted back to it."""


class LLMBudget:
    def __init__(self, consecutive: int = MAX_CONSECUTIVE_FAILURES, total: int = MAX_TOTAL_FAILURES):
        self.max_consecutive, self.max_total = consecutive, total
        self.consecutive = self.total = 0
        self.unavailable = False

    @property
    def exhausted(self) -> bool:
        return (self.unavailable or self.consecutive >= self.max_consecutive
                or self.total >= self.max_total)

    def failed(self) -> None:
        self.consecutive += 1
        self.total += 1

    def succeeded(self) -> None:
        self.consecutive = 0


def render_prompt(path: Path, **values: Any) -> str:
    """Fill a prompt template's $placeholders. A missing value raises KeyError."""
    return Template(path.read_text(encoding="utf-8").rstrip("\n")).substitute(values)


def ask_json(model: ModelAdapter, prompt: str, parse: Callable[[Any], T],
             budget: LLMBudget, purpose: str = "", images: Optional[list[bytes]] = None) -> Optional[T]:
    """Ask until `parse` accepts the reply or the budget runs out. With
    `images`, a model that refuses them raises ImagesUnsupported to the
    caller, which can ask again without them; that costs no budget."""
    mistake = None
    while not budget.exhausted:
        full = prompt
        if mistake:
            full += (f"\n\nYour previous reply could not be used: {mistake}\n"
                     "Do not repeat this mistake. Reply with the JSON object only.")
        started = time.perf_counter()
        record("llm_start", purpose=purpose, model=getattr(model, "model", "none"),
               request_number=budget.total + 1, images=len(images or ()))
        if hasattr(model, "last_call"):
            model.last_call = None

        def log(outcome, reply=None, error=None):
            meta = getattr(model, "last_call", None) or {}
            record("llm_call", purpose=purpose, outcome=outcome, prompt=full, reply=reply,
                   error=error, images=len(images or ()), reasoning=meta.get("reasoning"),
                   usage=meta.get("usage"),
                   model=meta.get("model"), finish_reason=meta.get("finish_reason"),
                   seconds=round(time.perf_counter() - started, 2),
                   failures={"consecutive": budget.consecutive, "total": budget.total})

        try:
            reply = model.complete(full, images=images) if images else model.complete(full)
        except ImagesUnsupported as exc:
            log("images_unsupported", error=str(exc))
            raise
        except ModelUnavailable as exc:
            budget.unavailable = True
            log("unavailable", error=str(exc))
            return None
        except Exception as exc:  # a crashing backend is an LLM failure, not a design failure
            mistake = f"the model call failed ({exc})"
            budget.failed()
            log("llm_failure", error=mistake)
            continue
        meta = getattr(model, "last_call", None) or {}
        if str(meta.get("finish_reason", "")).lower() in ("length", "max_tokens"):
            budget.failed()
            recover = getattr(model, "recover_truncation", None)
            recovered = bool(recover and recover())
            error = ("Reply hit the token limit before a complete answer. Retrying once with "
                     "Qwen thinking disabled and at least 8192 output tokens." if recovered else
                     "Reply hit the token limit before a complete answer. Stopped to avoid repeating "
                     "the same request. Choose reasoning off/minimal/low or increase the reply token cap.")
            log("llm_failure", reply=reply, error=error)
            record("model_recovery", recovered=recovered, reason=error,
                   reasoning=getattr(model, "reasoning", None), max_tokens=getattr(model, "max_tokens", None))
            if recovered:
                mistake = "Return a compact, complete JSON answer directly. Your previous output was truncated."
                continue
            budget.unavailable = True
            return None
        try:
            result = parse(_extract_json(reply))
        except UnusableReply as exc:
            mistake = str(exc)
            budget.failed()
            log("llm_failure", reply=reply, error=mistake)
            continue
        budget.succeeded()
        log("usable", reply=reply)
        return result
    return None


def _extract_json(reply: str) -> Any:
    text = (reply or "").strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise UnusableReply("the reply contained no JSON object")
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise UnusableReply(f"the reply was not valid JSON ({exc})") from exc
