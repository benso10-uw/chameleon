"""Asking the model for JSON, with the per-design LLM-failure budget.

An LLM failure is a reply that can't be used at all. It is retried with
the mistake quoted back, and counts against its own budget (3 in a row or
6 in total per design), never against the design-attempt cap. An
unreachable model ends the budget at once, since retrying can't help.
"""

import json
import re
from typing import Any, Callable, Optional, TypeVar

from engine.model import ModelAdapter, ModelUnavailable

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


def ask_json(model: ModelAdapter, prompt: str, parse: Callable[[Any], T],
             budget: LLMBudget) -> Optional[T]:
    """Ask until `parse` accepts the reply or the budget runs out."""
    mistake = None
    while not budget.exhausted:
        full = prompt
        if mistake:
            full += (f"\n\nYour previous reply could not be used: {mistake}\n"
                     "Do not repeat this mistake. Reply with the JSON object only.")
        try:
            reply = model.complete(full)
        except ModelUnavailable:
            budget.unavailable = True
            return None
        except Exception as exc:  # a crashing backend is an LLM failure, not a design failure
            mistake = f"the model call failed ({exc})"
            budget.failed()
            continue
        try:
            result = parse(_extract_json(reply))
        except UnusableReply as exc:
            mistake = str(exc)
            budget.failed()
            continue
        budget.succeeded()
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
