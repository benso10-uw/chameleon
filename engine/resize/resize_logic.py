"""
THIS IS THE FILE YOU IMPLEMENT.

Everything else in this repo (input loading, output writing, rendering,
the Dockerfile) is plumbing so you can focus on exactly one function:
`resize()`. See RULES.md and the design schema for the full contract.

The engine follows ADR-0001 (docs/adr/0001-llm-generates-hard-layouts.md):

1. Understand — code breaks the design down and measures it; the LLM
   assigns roles, priorities and difficulty (heuristics if it can't).
2. Lay out — easy designs get the preset layout; hard designs, and easy
   ones whose preset layout still breaks a rule, go to the LLM.
3. Check loop — the LLM's attempts are checked, reported back and ranked;
   the best attempt or the preset layout is returned.
"""

import copy
import traceback
from typing import Any, Optional

from engine.analyze.breakdown import infer_roles
from engine.analyze.understand import understand
from engine.check.checker import check
from engine.model.llm import LLMBudget
from engine.model.model import ModelAdapter, NoModel, default_model
from engine.resize.hard_path import Candidate, run_check_loop
from engine.resize.preset import preset_layout
from engine.resize.repair import repair
from engine.tracing.trace import Trace, record, tracing


def resize(source: dict[str, Any], target_canvas: dict[str, Any],
           model: Optional[ModelAdapter] = None, trace: Optional[Trace] = None, *,
           use_model_understanding: bool = True, refine: bool = False,
           max_attempts: int = 3, llm_tiebreak: bool = True) -> dict[str, Any]:
    """`trace`, if given, receives a record of every step (see engine/tracing/trace.py)."""
    target = {"width": target_canvas["width"], "height": target_canvas["height"]}
    with tracing(trace):
        try:
            return _resize(source, target, model or default_model(), use_model_understanding,
                           refine, max_attempts, llm_tiebreak)
        except Exception as exc:
            # Never let one design take the whole run down.
            record("error", error=repr(exc), traceback=traceback.format_exc())
            layout = _safe_layout(source, target)
            record("result", winner="last-resort layout", reason="the engine raised an error",
                   layout=layout)
            return layout


def _resize(source: dict[str, Any], target: dict[str, Any], model: ModelAdapter,
            use_model_understanding=True, refine=False, max_attempts=3, llm_tiebreak=True) -> dict[str, Any]:
    budget = LLMBudget()
    if isinstance(model, NoModel):
        budget.unavailable = True
    understanding = understand(source, target, model, budget, use_model=use_model_understanding)

    layout = repair(source, preset_layout(source, target, understanding), target, understanding)
    preset = Candidate("preset", layout,
                       check(source, layout, target, understanding.roles, understanding.priorities))
    easy = understanding.difficulty == "easy" and not refine
    if easy and preset.report.passes:
        decision = "easy and passes every rule: returned without the LLM"
    elif easy:
        decision = "easy but breaks a rule: promoted to hard"
    else:
        decision = "hard: the LLM lays it out from scratch"
    record("preset", layout=preset.layout, report=preset.report.to_dict(), decision=decision)
    if easy and preset.report.passes:
        record("result", winner="preset", reason=decision, layout=preset.layout,
               report=preset.report.to_dict())
        return preset.layout

    understanding.difficulty = "hard"  # promoted, if it was easy
    best = run_check_loop(source, target, understanding, preset, model, budget,
                          max_attempts=max_attempts, llm_tiebreak=llm_tiebreak)
    reason = ("no design attempt beat it" if best is preset
              else "best design attempt by rule cost, then signals")
    record("result", winner=best.name, reason=reason, layout=best.layout,
           report=best.report.to_dict())
    return best.layout


def _safe_layout(source: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    """Last-resort layout if the engine itself fails: the whole design scaled
    uniformly to fit and centred, with backgrounds covering the canvas."""
    W, H = target["width"], target["height"]
    sW, sH = source["canvas"]["width"], source["canvas"]["height"]
    roles = infer_roles(source)
    scale = min(W / sW, H / sH)
    dx, dy = (W - sW * scale) / 2, (H - sH * scale) / 2
    elements = []
    for el in source["elements"]:
        out = copy.deepcopy(el)
        if roles.get(el["id"]) == "background":
            out.update(x=0, y=0, width=W, height=H)
        else:
            out.update(x=dx + el["x"] * scale, y=dy + el["y"] * scale,
                       width=el["width"] * scale, height=el["height"] * scale)
            if el["type"] == "text":
                out["font_size"] = max(1.0, el["font_size"] * scale)
        elements.append(out)
    return {"canvas": {"width": W, "height": H}, "elements": elements}
