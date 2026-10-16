"""Task traces: an ordered record of every step `resize()` takes.

Tracing is off unless a Trace is passed to `resize()`; `record()` is then a
no-op, so scoring runs pay nothing. Steps are plain JSON-ready dicts:

- understand   measurements, heuristic and final roles/priorities, difficulty
- llm_call     purpose, prompt, reply, reasoning, tokens, seconds, outcome
- preset       the preset layout, its violation report, and the routing decision
- attempt      a design attempt's layout, report, and whether it improved / is best
- tie_break    the two candidates compared and the verdict
- result       which candidate was returned and why
- error        an exception that sent the engine to its last-resort layout
"""

import contextvars
import copy
import json
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

_current: contextvars.ContextVar[Optional["Trace"]] = contextvars.ContextVar("chameleon_trace",
                                                                           default=None)


class Trace:
    def __init__(self, task: str = ""):
        self.task = task
        self.steps: list[dict[str, Any]] = []
        self._started = time.perf_counter()

    def add(self, kind: str, **data: Any) -> None:
        elapsed = round(time.perf_counter() - self._started, 3)
        self.steps.append({"kind": kind, "at_seconds": elapsed, **copy.deepcopy(data)})

    def to_dict(self) -> dict[str, Any]:
        return {"task": self.task, "steps": self.steps}

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False, default=str),
                        encoding="utf-8")


@contextmanager
def tracing(trace: Optional[Trace]) -> Iterator[None]:
    token = _current.set(trace)
    try:
        yield
    finally:
        _current.reset(token)


def record(kind: str, **data: Any) -> None:
    trace = _current.get()
    if trace is not None:
        trace.add(kind, **data)
