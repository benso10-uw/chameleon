"""Hard path: the LLM lays the design out from scratch inside the check loop.

Each design attempt is repaired in code, checked, and its violation report
sent back for the next attempt. At most 3 design attempts; the loop stops
early when an attempt doesn't improve on the previous one. Code ranks
layouts by rule violations; when two tie, the LLM weighs their signals.
The best attempt is returned, or the preset layout if none beats it — even
if that preset layout breaks a rule (ADR-0001).
"""

import copy
import json
from dataclasses import dataclass
from typing import Any, Optional

from engine.breakdown import Understanding
from engine.checker import ViolationReport, check
from engine.llm import LLMBudget, UnusableReply, ask_json
from engine.model import ModelAdapter
from engine.repair import repair
from engine.understand import element_facts

MAX_DESIGN_ATTEMPTS = 3
COST_EPSILON = 1e-6
GEOMETRY = ("x", "y", "width", "height")


@dataclass
class Candidate:
    name: str
    layout: dict[str, Any]
    report: ViolationReport


def run_check_loop(source: dict[str, Any], target: dict[str, Any], understanding: Understanding,
                   preset: Candidate, model: ModelAdapter, budget: LLMBudget,
                   max_attempts: int = MAX_DESIGN_ATTEMPTS) -> Candidate:
    best = preset
    previous: Optional[Candidate] = None
    for n in range(1, max_attempts + 1):
        prompt = _layout_prompt(source, target, understanding, previous)
        layout = ask_json(model, prompt, lambda reply: _apply(source, target, reply), budget)
        if layout is None:
            break  # LLM-failure budget spent or model unavailable
        layout = repair(source, layout, target, understanding)
        attempt = Candidate(f"attempt {n}",
                            layout, check(source, layout, target, understanding.roles,
                                          understanding.priorities))
        improved = previous is None or _better(attempt, previous, model, budget)
        if _better(attempt, best, model, budget):
            best = attempt
        if not improved:
            break
        previous = attempt
    return best


def _better(a: Candidate, b: Candidate, model: ModelAdapter, budget: LLMBudget) -> bool:
    """Is `a` better than `b`? Rules decide; signals break ties."""
    if a.report.rule_cost < b.report.rule_cost - COST_EPSILON:
        return True
    if a.report.rule_cost > b.report.rule_cost + COST_EPSILON:
        return False
    verdict = _llm_tie_break(a, b, model, budget)
    if verdict is not None:
        return verdict
    return a.report.signal_cost < b.report.signal_cost - COST_EPSILON


def _llm_tie_break(a: Candidate, b: Candidate, model: ModelAdapter, budget: LLMBudget) -> Optional[bool]:
    if not a.report.signals and not b.report.signals:
        return False
    prompt = f"""Two layouts of the same design both satisfy the same hard rules equally well.
Judge which looks more designed from their quality signals (unintended overlap, small text,
hierarchy, empty space). Signals are observations, not automatic failures: some overlaps
look intentional and fine.

Layout A signals: {json.dumps([_signal_summary(v) for v in a.report.signals])}
Layout B signals: {json.dumps([_signal_summary(v) for v in b.report.signals])}

Reply with JSON only: {{"better": "A" | "B"}}"""

    def parse(reply):
        choice = reply.get("better") if isinstance(reply, dict) else None
        if choice not in ("A", "B"):
            raise UnusableReply('reply must be {"better": "A"} or {"better": "B"}')
        return choice == "A"

    return ask_json(model, prompt, parse, budget)


def _signal_summary(v) -> dict[str, Any]:
    return {"kind": v.kind, "element": v.element_id, "severity": v.severity, "priority": v.priority}


def _apply(source: dict[str, Any], target: dict[str, Any], reply: Any) -> dict[str, Any]:
    """Turn the LLM's reply into a layout. Only geometry, font size and
    rotation come from the model; every other field is copied from the
    source, so protected fields are restored silently."""
    if not isinstance(reply, dict) or not isinstance(reply.get("elements"), list):
        raise UnusableReply('expected {"elements": [{"id", "x", "y", "width", "height", ...}]}')
    src_by_id = {el["id"]: el for el in source["elements"]}
    got: dict[str, dict[str, Any]] = {}
    for item in reply["elements"]:
        if not isinstance(item, dict) or "id" not in item:
            raise UnusableReply("every entry in elements needs an id")
        if item["id"] in got:
            raise UnusableReply(f"element '{item['id']}' appears more than once")
        got[item["id"]] = item
    missing, extra = set(src_by_id) - set(got), set(got) - set(src_by_id)
    if missing or extra:
        raise UnusableReply(f"element ids must match the source exactly; missing {sorted(missing)}, "
                            f"unknown {sorted(extra)}")

    elements = []
    for el in source["elements"]:
        item, out = got[el["id"]], copy.deepcopy(el)
        for k in GEOMETRY:
            if not isinstance(item.get(k), (int, float)):
                raise UnusableReply(f"element '{el['id']}' needs a numeric {k}")
            out[k] = float(item[k])
        if el["type"] == "text" and isinstance(item.get("font_size"), (int, float)):
            out["font_size"] = float(item["font_size"])
        if isinstance(item.get("rotation"), (int, float)):
            out["rotation"] = float(item["rotation"])
        elements.append(out)
    return {"canvas": {"width": target["width"], "height": target["height"]}, "elements": elements}


def _layout_prompt(source, target, understanding: Understanding, previous: Optional[Candidate]) -> str:
    facts = element_facts(source)
    for f in facts:
        f["role"] = understanding.roles.get(f["id"])
        f["priority"] = understanding.priorities.get(f["id"])
    W, H = target["width"], target["height"]
    prompt = f"""You are a senior graphic designer re-laying-out a design for a new canvas.
Rearrange it — move, resize and reflow elements — so it looks designed for the new shape.
Do not just stretch or shrink everything uniformly.

Source canvas: {source["canvas"]["width"]}x{source["canvas"]["height"]}
Target canvas: {W}x{H} (aspect ratio {W / H:.2f})
Difficulty: {understanding.difficulty}
Measurements: {json.dumps(understanding.measurements)}

Elements (box is [x, y, width, height] in source px; priority 1 = most important):
{json.dumps(facts, ensure_ascii=False)}

Rules every layout must satisfy:
- Return every element id exactly once. Never drop, hide or invent an element.
- Every element must lie fully inside 0..{W} x 0..{H}, including rotated elements' corners.
- Images keep their source aspect ratio (within 5%); a fit "cover" background should fill the canvas.
- Elements sharing a group_id keep their relative arrangement: scale and move them together.
- Text must fit its box at its font size; font size never below 6px, and 10px or more except for
  the lowest-priority text (e.g. legal).
Quality goals: no unintended overlaps between content (decoration/background layering is fine),
the most important elements largest and most prominent, aligned edges, even spacing.
When space is tight: shrink low-priority elements first, then let them overlap only the
background or decoration, and only then take the lowest-priority text down to 6px.

Reply with JSON only, giving new geometry for every element (font_size for text):
{{"elements": [{{"id": "...", "x": 0, "y": 0, "width": 0, "height": 0, "font_size": 0}}, ...]}}"""
    if previous is not None:
        geometry = [{k: el[k] for k in ("id",) + GEOMETRY + ("font_size",) if k in el}
                    for el in previous.layout["elements"]]
        prompt += f"""

Your previous attempt:
{json.dumps(geometry)}
Its violation report (rules must be fixed first, highest-priority elements first; displacement
is how far an element moved from its source position, as a fraction of the canvas):
{json.dumps(previous.report.to_dict())}
Produce an improved layout that fixes these."""
    return prompt
