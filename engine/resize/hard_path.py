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
import math
from dataclasses import dataclass
from typing import Any, Optional

from engine.analyze.breakdown import Understanding, bbox, groups_of, union
from engine.analyze.text_metrics import measure_text
from engine.analyze.understand import element_facts
from engine.check.checker import ViolationReport, check
from engine.model.llm import LLMBudget, UnusableReply, ask_json
from engine.model.model import ModelAdapter
from engine.resize.repair import repair
from engine.tracing.trace import record

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
                   max_attempts: int = MAX_DESIGN_ATTEMPTS, llm_tiebreak: bool = True) -> Candidate:
    best = preset
    previous: Optional[Candidate] = None
    for n in range(1, max_attempts + 1):
        prompt = _layout_prompt(source, target, understanding, previous)
        layout = ask_json(model, prompt, lambda reply: _apply(source, target, reply), budget,
                          purpose=f"layout attempt {n}")
        if layout is None:
            record("loop_end", reason="LLM-failure budget spent or model unavailable",
                   attempts=n - 1)
            break
        layout = repair(source, layout, target, understanding)
        attempt = Candidate(f"attempt {n}",
                            layout, check(source, layout, target, understanding.roles,
                                          understanding.priorities))
        judge = model if llm_tiebreak else None
        improved = previous is None or _better(attempt, previous, judge, budget)
        # Reuse the same comparison when the previous attempt is already best.
        new_best = improved if previous is best else _better(attempt, best, judge, budget)
        if new_best:
            best = attempt
        record("attempt", name=attempt.name, layout=attempt.layout, report=attempt.report.to_dict(),
               improved_on_previous=improved, new_best=new_best, best_so_far=best.name)
        if not improved:
            record("loop_end", reason=f"{attempt.name} did not improve on the previous attempt",
                   attempts=n)
            break
        previous = attempt
    else:
        record("loop_end", reason=f"reached the cap of {max_attempts} design attempts",
               attempts=max_attempts)
    return best


def _better(a: Candidate, b: Candidate, model: ModelAdapter, budget: LLMBudget) -> bool:
    """Is `a` better than `b`? Rules decide; signals break ties."""
    if a.report.rule_cost < b.report.rule_cost - COST_EPSILON:
        return True
    if a.report.rule_cost > b.report.rule_cost + COST_EPSILON:
        return False
    verdict = _llm_tie_break(a, b, model, budget) if model is not None else None
    decided_by = "llm"
    if verdict is None:
        verdict = a.report.signal_cost < b.report.signal_cost - COST_EPSILON
        decided_by = ("signal cost (no usable LLM verdict)" if a.report.signals or b.report.signals
                      else "no signals on either: keep the earlier")
    record("tie_break", a=a.name, b=b.name, rule_cost=a.report.rule_cost,
           signal_cost={a.name: a.report.signal_cost, b.name: b.report.signal_cost},
           winner=a.name if verdict else b.name, decided_by=decided_by)
    return verdict


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

    return ask_json(model, prompt, parse, budget, purpose=f"tie-break: {a.name} vs {b.name}")


def _signal_summary(v) -> dict[str, Any]:
    return {"kind": v.kind, "element": v.element_id, "severity": v.severity, "priority": v.priority}


def _apply(source: dict[str, Any], target: dict[str, Any], reply: Any) -> dict[str, Any]:
    """Turn the LLM's reply into a layout. Only geometry, font size and
    rotation come from the model; every other field is copied from the
    source, so protected fields are restored silently."""
    if not isinstance(reply, dict) or not isinstance(reply.get("elements"), list):
        raise UnusableReply('expected {"elements": [{"id", "x", "y", "width", "height", ...}]}')
    reply = _expand_groups(source, reply)
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
            if not _number(item.get(k)):
                raise UnusableReply(f"element '{el['id']}' needs a numeric {k}")
            out[k] = float(item[k])
        if out["width"] <= 0 or out["height"] <= 0:
            raise UnusableReply(f"element '{el['id']}' needs positive dimensions")
        if el["type"] == "image" and el.get("fit") != "cover":
            # A model proposes a placement box; contain the source ratio inside it.
            scale = min(out["width"] / el["width"], out["height"] / el["height"])
            w, h = el["width"] * scale, el["height"] * scale
            out["x"] += (out["width"]-w)/2
            out["y"] += (out["height"]-h)/2
            out["width"], out["height"] = w, h
        if el["type"] == "text" and isinstance(item.get("font_size"), (int, float)):
            out["font_size"] = float(item["font_size"])
        if isinstance(item.get("rotation"), (int, float)):
            out["rotation"] = float(item["rotation"])
        if not all(_number(out[k]) for k in ("font_size", "rotation") if k in out):
            raise UnusableReply(f"element '{el['id']}' has a non-finite font size or rotation")
        elements.append(out)
    return {"canvas": {"width": target["width"], "height": target["height"]}, "elements": elements}


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _expand_groups(source, reply):
    """Let the model position each group once; code owns its internal geometry."""
    if "groups" not in reply:
        return reply  # Existing adapters and scripted tests may return all elements.
    groups = groups_of(source["elements"])
    if not isinstance(reply["groups"], list):
        raise UnusableReply("groups must be a list")
    elements, seen = list(reply["elements"]), set()
    for item in reply["groups"]:
        if not isinstance(item, dict) or not isinstance(item.get("group_id"), str):
            raise UnusableReply("every group needs a group_id")
        gid = item["group_id"]
        if gid not in groups or gid in seen:
            raise UnusableReply(f"unknown or duplicate group {gid}")
        seen.add(gid)
        if not all(_number(item.get(k)) for k in ("x", "y", "scale")) or item["scale"] <= 0:
            raise UnusableReply(f"group {gid} needs finite x/y and a positive scale")
        x0, y0, _, _ = union(bbox(m) for m in groups[gid])
        minimum = max((6/m["font_size"] for m in groups[gid] if m["type"] == "text"), default=0)
        scale = max(item["scale"], minimum)
        for m in groups[gid]:
            out = {"id": m["id"], "x": item["x"] + (m["x"] - x0) * scale,
                   "y": item["y"] + (m["y"] - y0) * scale,
                   "width": m["width"] * scale, "height": m["height"] * scale}
            if m["type"] == "text":
                out["font_size"] = m["font_size"] * scale
            elements.append(out)
    if seen != set(groups):
        raise UnusableReply(f"missing groups {sorted(set(groups) - seen)}")
    return {"elements": elements}


def _layout_prompt(source, target, understanding: Understanding, previous: Optional[Candidate]) -> str:
    facts = element_facts(source)
    for f in facts:
        f["role"] = understanding.roles.get(f["id"])
        f["priority"] = understanding.priorities.get(f["id"])
    W, H = target["width"], target["height"]
    groups = groups_of(source["elements"])
    group_facts = []
    for gid, members in groups.items():
        x0, y0, x1, y1 = union(bbox(m) for m in members)
        labels = [m for m in members if m["type"] == "text"]
        group_facts.append({"group_id": gid, "source_size": [round(x1-x0, 2), round(y1-y0, 2)],
                            "labels": [m["content"] for m in labels],
                            "readable_scale": round(max((10/m["font_size"] for m in labels), default=0.1), 3),
                            "role": understanding.roles[members[0]["id"]],
                            "minimum_scale_6px": round(max((6/m["font_size"] for m in labels), default=.1), 3)})
    for fact, el in zip(facts, source["elements"]):
        if el["type"] == "text":
            fact.update(font_family=el["font_family"], line_height=el.get("line_height", 1.2),
                        text=el["content"], text_align=el.get("text_align", "left"))
            fact["width_at_10px_one_line"] = round(measure_text(el, font_size=10, width=float("inf")).widest_line)
            # Measured alternatives replace the model's unreliable glyph arithmetic.
            fact["fit_options"] = [{"width": round(W*share), "font_size": size,
                                    "height_needed": round(measure_text(el, font_size=size, width=round(W*share)).height, 1)}
                                   for share in (.5, .7, .87) for size in (6, 8, 10, 12, 14)]
    facts = [f for f in facts if not f.get("group_id")]
    prompt = f"""You are a senior graphic designer re-laying-out a design for a new canvas.
Rearrange it — move, resize and reflow elements — so it looks designed for the new shape.
Do not just stretch or shrink everything uniformly.

Source canvas: {source["canvas"]["width"]}x{source["canvas"]["height"]}
Target canvas: {W}x{H} (aspect ratio {W / H:.2f})
Difficulty: {understanding.difficulty}
Measurements: {json.dumps(understanding.measurements)}

Art direction:
- Establish one clear reading path: brand/product, headline, supporting copy, action.
- Choose a small set of aligned zones before assigning coordinates. Reserve 4% margins and consistent gaps.
- On a wide banner use the WIDTH: a compact brand/product zone, a broad copy zone, and an action/badge zone.
  Do not cram long copy into a narrow column. A 90px-high canvas needs short rows, never a poster stack.
- Preserve the headline's prominence. Measure capacity: estimated lines = ceil(single-line width / box width);
  required height = lines * font_size * line_height. Leave 10% slack for word wrapping.
  fit_options gives REAL FONT measurements at candidate widths/sizes; use these instead of guessing.
  On crowded banners aim for a headline >=10px and a supporting line at 6–8px before sacrificing the headline.
- Keep buttons and badges as complete groups. Code computes their labels, font sizes and source rotations.
  You choose each group's top-left ROTATED bounding-box position and a single uniform scale.
  Code clamps scale to minimum_scale_6px; reserve at least source_size * minimum_scale_6px per group.
  Pack groups into a separate bottom rail when the canvas has multiple badges; give long copy the width above it.
- Image width/height is a placement box: code contains the source ratio inside it. Prefer a generous product box.
- Keep every element and the exact full copy. Never abbreviate text, invent imagery, or hide an element.
- Use the image's source ratio. Keep background full bleed; reserve small secondary zones for watermarks/legal.
- Return compact JSON directly; do not narrate coordinate arithmetic or repeat the brief.

Ungrouped elements (box is [x, y, width, height] in source px; priority 1 = most important):
{json.dumps(facts, ensure_ascii=False)}

Rigid groups (return each group ONCE in groups, NEVER its member ids in elements):
{json.dumps(group_facts, ensure_ascii=False)}

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
{{"elements": [{{"id": "...", "x": 0, "y": 0, "width": 100, "height": 20, "font_size": 12}}],
 "groups": [{{"group_id": "...", "x": 0, "y": 0, "scale": 0.7}}]}}"""
    if previous is not None:
        geometry = [{k: el[k] for k in ("id",) + GEOMETRY + ("font_size",) if k in el}
                    for el in previous.layout["elements"]]
        compact_report = {kind: [{"kind": v.kind, "id": v.element_id, "message": v.message}
                                for v in getattr(previous.report, kind)] for kind in ("rules", "signals")}
        prompt += f"""

Your previous attempt:
{json.dumps(geometry)}
Its violation report (rules must be fixed first, highest-priority elements first; displacement
is how far an element moved from its source position, as a fraction of the canvas):
{json.dumps(compact_report)}
Produce an improved layout that fixes these."""
    return prompt
