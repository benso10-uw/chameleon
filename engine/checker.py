"""The checker: judges a layout against the source design and target canvas.

Returns a violation report split into rules (must hold for every returned
layout) and signals (quality observations that inform judgement but never
reject a layout on their own). Each violation says what broke, how badly,
how far the element moved from its source position, and its priority.
"""

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from engine.breakdown import (LAYERING_ROLES, bbox, groups_of, infer_priorities, infer_roles,
                              intersection_area, text_floor, union)
from engine.text_metrics import measure_text

REQUIRED_COMMON = ("id", "type", "x", "y", "width", "height")
REQUIRED_BY_TYPE = {
    "text": ("content", "font_family", "font_size", "color"),
    "image": ("asset_id",),
    "shape": ("shape_type", "fill_color"),
}
# Fields only the source design decides; layouts may change geometry and font size.
PROTECTED_FIELDS = ("type", "content", "font_family", "font_weight", "font_style", "color",
                    "style_runs", "asset_id", "fit", "shape_type", "fill_color", "stroke_color",
                    "stroke_width", "corner_radius", "z_index", "group_id", "opacity",
                    "text_align", "line_height", "letter_spacing")

CANVAS_TOLERANCE = 0.5  # px
ASPECT_TOLERANCE = 0.05
GROUP_TOLERANCE = 0.05
OVERFLOW_TOLERANCE = 1.0  # px
ABSOLUTE_TEXT_FLOOR = 6.0
OVERLAP_MIN_FRACTION = 0.02
SPACE_USE_MIN = 0.45


@dataclass
class Violation:
    kind: str
    element_id: Optional[str]
    severity: float
    message: str
    priority: Optional[int] = None
    displacement: Optional[float] = None


@dataclass
class ViolationReport:
    rules: list[Violation] = field(default_factory=list)
    signals: list[Violation] = field(default_factory=list)
    element_count: int = 1

    @property
    def passes(self) -> bool:
        return not self.rules

    def _cost(self, violations: list[Violation]) -> float:
        n = max(self.element_count, 1)
        total = 0.0
        for v in violations:
            rank = v.priority if v.priority is not None else n
            total += v.severity * (1 + (n - rank + 1) / n)
        return round(total, 6)

    @property
    def rule_cost(self) -> float:
        """Rule violations weighted by severity and element priority."""
        return self._cost(self.rules)

    @property
    def signal_cost(self) -> float:
        return self._cost(self.signals)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passes": self.passes,
            "rule_cost": self.rule_cost,
            "signal_cost": self.signal_cost,
            "rules": [asdict(v) for v in self.rules],
            "signals": [asdict(v) for v in self.signals],
        }


def check(source: dict[str, Any], layout: dict[str, Any], target_canvas: dict[str, Any],
          roles: Optional[dict[str, str]] = None,
          priorities: Optional[dict[str, int]] = None) -> ViolationReport:
    roles = roles or infer_roles(source)
    priorities = priorities or infer_priorities(source, roles)
    src_by_id = {el["id"]: el for el in source["elements"]}
    report = ViolationReport(element_count=len(src_by_id))
    W, H = target_canvas["width"], target_canvas["height"]
    sW, sH = source["canvas"]["width"], source["canvas"]["height"]

    def rule(kind, eid, severity, message):
        report.rules.append(Violation(kind, eid, round(severity, 4), message,
                                      priorities.get(eid), displacement(eid)))

    def signal(kind, eid, severity, message):
        report.signals.append(Violation(kind, eid, round(severity, 4), message,
                                        priorities.get(eid), displacement(eid)))

    def displacement(eid):
        src, out = src_by_id.get(eid), out_by_id.get(eid)
        if not src or not out or not _has_geometry(out):
            return None
        (a0, b0, a1, b1), (c0, d0, c1, d1) = bbox(src), bbox(out)
        dx = (c0 + c1) / 2 / W - (a0 + a1) / 2 / sW
        dy = (d0 + d1) / 2 / H - (b0 + b1) / 2 / sH
        return round(math.hypot(dx, dy), 4)

    elements = layout.get("elements") or []
    out_by_id: dict[str, dict[str, Any]] = {}

    # --- Rules: every element present exactly once ------------------------
    for el in elements:
        eid = el.get("id")
        if eid in out_by_id:
            rule("duplicate_element", eid, 1.0, f"'{eid}' appears more than once")
        elif eid not in src_by_id:
            rule("extra_element", eid, 1.0, f"'{eid}' is not in the source design")
        else:
            out_by_id[eid] = el
    for eid in src_by_id:
        if eid not in out_by_id:
            rule("missing_element", eid, 1.0, f"'{eid}' is missing from the layout")

    for eid, el in out_by_id.items():
        src = src_by_id[eid]

        # --- Rules: required and protected fields -------------------------
        missing = [f for f in REQUIRED_COMMON + REQUIRED_BY_TYPE.get(src["type"], ()) if f not in el]
        if missing:
            rule("missing_field", eid, 1.0, f"'{eid}' is missing {missing}")
            continue
        changed = [f for f in PROTECTED_FIELDS if el.get(f) != src.get(f)]
        if changed:
            rule("changed_protected_field", eid, 1.0, f"'{eid}' changed {changed}")

        # --- Rules: still visible -----------------------------------------
        if el.get("opacity", 1) <= 0 or el["width"] < 1 or el["height"] < 1:
            rule("invisible", eid, 1.0, f"'{eid}' is not visible (zero opacity or size)")
            continue

        # --- Rules: nothing off the target canvas -------------------------
        x0, y0, x1, y1 = bbox(el)
        outside = (max(0, -x0) + max(0, x1 - W)) * (y1 - y0) + (max(0, -y0) + max(0, y1 - H)) * (x1 - x0)
        if (x0 < -CANVAS_TOLERANCE or y0 < -CANVAS_TOLERANCE
                or x1 > W + CANVAS_TOLERANCE or y1 > H + CANVAS_TOLERANCE):
            area = max((x1 - x0) * (y1 - y0), 1)
            rule("off_canvas", eid, min(1.0, outside / area),
                 f"'{eid}' extends outside the {W}x{H} canvas (bounds {x0:.0f},{y0:.0f},{x1:.0f},{y1:.0f})")

        # --- Rules: images keep their proportions -------------------------
        if src["type"] == "image" and src.get("fit") != "cover" and src["height"] and el["height"]:
            ratio = (el["width"] / el["height"]) / (src["width"] / src["height"])
            drift = abs(ratio - 1)
            if drift > ASPECT_TOLERANCE:
                rule("distorted_image", eid, min(1.0, drift),
                     f"'{eid}' aspect ratio drifted {drift:.0%} from the source")

        # --- Rules and signals: text ---------------------------------------
        if src["type"] == "text":
            size = float(el["font_size"])
            if size < ABSOLUTE_TEXT_FLOOR:
                rule("text_below_floor", eid, (ABSOLUTE_TEXT_FLOOR - size) / ABSOLUTE_TEXT_FLOOR,
                     f"'{eid}' font size {size:.1f}px is below the {ABSOLUTE_TEXT_FLOOR:.0f}px floor")
            elif size < text_floor(roles.get(eid, "body")):
                floor = text_floor(roles.get(eid, "body"))
                signal("small_text", eid, (floor - size) / floor,
                       f"'{eid}' font size {size:.1f}px is under the {floor:.0f}px readability floor")
            metrics = measure_text(el)
            over_h = metrics.height - el["height"]
            over_w = metrics.widest_line - el["width"]
            if over_h > OVERFLOW_TOLERANCE or over_w > OVERFLOW_TOLERANCE:
                severity = max(over_h / max(el["height"], 1), over_w / max(el["width"], 1))
                rule("text_overflow", eid, min(1.0, severity),
                     f"'{eid}' text needs {metrics.widest_line:.0f}x{metrics.height:.0f}px "
                     f"but its box is {el['width']:.0f}x{el['height']:.0f}px")

    # --- Rules: groups move as one ------------------------------------------
    for gid, members in groups_of(source["elements"]).items():
        if len(members) < 2 or any(m["id"] not in out_by_id or not _has_geometry(out_by_id[m["id"]])
                                   for m in members):
            continue
        deviation = _group_deviation(members, [out_by_id[m["id"]] for m in members])
        if deviation > GROUP_TOLERANCE:
            lead = min(members, key=lambda m: priorities.get(m["id"], 99))["id"]
            rule("group_broken", lead, min(1.0, deviation),
                 f"group '{gid}' members no longer keep their relative layout ({deviation:.0%} off)")

    # --- Signals: unintended overlap ------------------------------------------
    content = [eid for eid in out_by_id if roles.get(eid) not in LAYERING_ROLES
               and _has_geometry(out_by_id[eid])]
    for i, a in enumerate(content):
        for b in content[i + 1:]:
            ga, gb = src_by_id[a].get("group_id"), src_by_id[b].get("group_id")
            if ga and ga == gb:
                continue
            if intersection_area(bbox(src_by_id[a]), bbox(src_by_id[b])) > 0:
                continue  # overlapped in the source: intentional layering
            box_a, box_b = bbox(out_by_id[a]), bbox(out_by_id[b])
            inter = intersection_area(box_a, box_b)
            smaller = min(_area(box_a), _area(box_b)) or 1
            if inter / smaller > OVERLAP_MIN_FRACTION:
                lead = a if priorities.get(a, 99) <= priorities.get(b, 99) else b
                signal("overlap", lead, min(1.0, inter / smaller),
                       f"'{a}' and '{b}' overlap ({inter / smaller:.0%} of the smaller)")

    # --- Signals: hierarchy (relative text sizes keep their order) ------------
    texts = [eid for eid in content if src_by_id[eid]["type"] == "text"]
    for i, a in enumerate(texts):
        for b in texts[i + 1:]:
            sa, sb = src_by_id[a]["font_size"], src_by_id[b]["font_size"]
            oa, ob = out_by_id[a]["font_size"], out_by_id[b]["font_size"]
            if sa > sb * 1.05 and oa < ob * 0.95 or sb > sa * 1.05 and ob < oa * 0.95:
                big = a if sa > sb else b
                signal("hierarchy_inverted", big, 0.5,
                       f"'{a}' and '{b}' swapped which one is bigger")

    # --- Signals: space use ------------------------------------------------------
    if content:
        x0, y0, x1, y1 = union(bbox(out_by_id[eid]) for eid in content)
        x0, y0, x1, y1 = max(x0, 0), max(y0, 0), min(x1, W), min(y1, H)
        used = max(0.0, (x1 - x0) * (y1 - y0)) / (W * H)
        if used < SPACE_USE_MIN:
            signal("empty_space", None, SPACE_USE_MIN - used,
                   f"content spans only {used:.0%} of the canvas")

    return report


def _has_geometry(el: dict[str, Any]) -> bool:
    return all(isinstance(el.get(k), (int, float)) for k in ("x", "y", "width", "height"))


def _area(box) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def _group_deviation(src_members, out_members) -> float:
    """Largest change in any member's box relative to its group's bounds,
    after normalising both groups to unit size. Uniform scaling and moving
    the whole group are free; anything else is deviation."""
    def normalised(members):
        x0, y0, x1, y1 = union((m["x"], m["y"], m["x"] + m["width"], m["y"] + m["height"])
                               for m in members)
        w, h = max(x1 - x0, 1e-6), max(y1 - y0, 1e-6)
        return [((m["x"] - x0) / w, (m["y"] - y0) / h, m["width"] / w, m["height"] / h)
                for m in members], w / h

    src_norm, src_aspect = normalised(src_members)
    out_norm, out_aspect = normalised(out_members)
    worst = abs(out_aspect / src_aspect - 1)
    for s, o in zip(src_norm, out_norm):
        worst = max(worst, *(abs(a - b) for a, b in zip(s, o)))
    return worst
