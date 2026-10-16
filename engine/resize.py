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
from typing import Any, Optional

from engine.breakdown import infer_roles
from engine.checker import check
from engine.hard_path import Candidate, run_check_loop
from engine.llm import LLMBudget
from engine.model import ModelAdapter, default_model
from engine.preset import preset_layout
from engine.repair import repair
from engine.understand import understand


def resize(source: dict[str, Any], target_canvas: dict[str, Any],
           model: Optional[ModelAdapter] = None) -> dict[str, Any]:
    target = {"width": target_canvas["width"], "height": target_canvas["height"]}
    try:
        return _resize(source, target, model or default_model())
    except Exception:
        # Never let one design take the whole run down.
        return _safe_layout(source, target)


def _resize(source: dict[str, Any], target: dict[str, Any], model: ModelAdapter) -> dict[str, Any]:
    budget = LLMBudget()
    understanding = understand(source, target, model, budget)

    layout = repair(source, preset_layout(source, target, understanding), target, understanding)
    preset = Candidate("preset", layout,
                       check(source, layout, target, understanding.roles, understanding.priorities))
    if understanding.difficulty == "easy" and preset.report.passes:
        return preset.layout

    if understanding.difficulty == "easy":
        understanding.difficulty = "hard"  # promoted: its preset layout still breaks a rule
    return run_check_loop(source, target, understanding, preset, model, budget).layout


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
