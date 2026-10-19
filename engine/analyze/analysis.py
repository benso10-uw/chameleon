"""Design analysis: the structured account of how a source design works.

The design-analysis call returns colours, the composition pattern, a short
description and the reason behind each element's priority; code adds the technical facts it
can measure exactly (position, size, layer, font size, colour) and the
spatial relationships between elements. The result travels with the
Understanding into every layout prompt, so the layout model keeps the
design's feeling while changing its arrangement.
"""

from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from engine.analyze.breakdown import LAYERING_ROLES, bbox, union

# Composition patterns the model chooses from (free text is kept but these
# are suggested so attempts and traces stay comparable).
PATTERNS = ("centered", "symmetrical", "asymmetrical_focal", "rule_of_thirds", "z_pattern",
            "f_pattern", "split_screen", "grid", "hero_with_rail", "stacked", "layered_overlay",
            "diagonal", "frame", "horizontal_flow")
SCHEMES = ("monochromatic", "analogous", "complementary", "split_complementary", "triadic",
           "tetradic", "neutral", "accented_neutral")
RELATIONS = ("below", "above", "left_of", "right_of", "inside", "overlaps", "aligned_with",
             "grouped_with", "near")

STACK_GAP = 0.12        # of the canvas height: how far below still reads as "under"
SIDE_GAP = 0.12         # of the canvas width
MIN_SHARED_SPAN = 0.4   # of the narrower element, to count as stacked / side by side
MAX_RELATIONSHIPS = 24


@dataclass
class DesignAnalysis:
    visual_input: str                      # "image" | "palette" | "text" | "none"
    decided_by: str                        # "llm" | "heuristics"
    palette: list[dict[str, Any]] = field(default_factory=list)   # {hex, name, usage}
    color_scheme: str = ""
    color_notes: str = ""
    pattern: str = ""
    pattern_notes: str = ""
    target_pattern: str = ""
    target_pattern_notes: str = ""
    description: str = ""
    priority_reasons: dict[str, str] = field(default_factory=dict)      # id -> why it matters
    relationships: list[dict[str, Any]] = field(default_factory=list)  # {a, relation, b, note}
    technical: list[dict[str, Any]] = field(default_factory=list)
    extracted_colours: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def brief(self) -> dict[str, Any]:
        """What the layout prompt needs: the design intent, not the raw facts
        it already lists per element."""
        return {k: v for k, v in {
            "description": self.description,
            "colours": {"palette": self.palette, "scheme": self.color_scheme, "notes": self.color_notes},
            "source_pattern": {"name": self.pattern, "notes": self.pattern_notes},
            "suggested_target_pattern": {"name": self.target_pattern, "notes": self.target_pattern_notes},
            "relationships": self.relationships,
        }.items() if v and v != {"name": "", "notes": ""}}


def technical_facts(source: dict[str, Any], roles: dict[str, str],
                    priorities: dict[str, int]) -> list[dict[str, Any]]:
    """Exact per-element facts, measured in code so the model never has to
    echo them back."""
    out = []
    for el in sorted(source["elements"], key=lambda e: priorities.get(e["id"], 99)):
        fact = {"id": el["id"], "type": el["type"], "role": roles.get(el["id"]),
                "priority": priorities.get(el["id"]),
                "x": round(el["x"], 1), "y": round(el["y"], 1),
                "width": round(el["width"], 1), "height": round(el["height"], 1),
                "z_index": el.get("z_index", 0)}
        for k in ("rotation", "font_size", "font_weight", "color", "fill_color", "group_id", "asset_id"):
            if el.get(k):
                fact[k] = el[k]
        out.append(fact)
    return out


def infer_relationships(source: dict[str, Any], roles: dict[str, str]) -> list[dict[str, Any]]:
    """Spatial relationships code can see between content units (ungrouped
    elements, and each group as one unit named by its group_id): stacked
    under, beside or inside one another. The model refines these."""
    W, H = source["canvas"]["width"], source["canvas"]["height"]
    units: dict[str, tuple[float, float, float, float]] = {}
    for el in source["elements"]:
        if roles.get(el["id"]) in LAYERING_ROLES:
            continue
        key = el.get("group_id") or el["id"]
        box = bbox(el)
        old = units.get(key)
        units[key] = box if old is None else union([old, box])
    found: list[dict[str, Any]] = []
    for a, (ax0, ay0, ax1, ay1) in units.items():
        for b, (bx0, by0, bx1, by1) in units.items():
            if a == b:
                continue
            if ax0 >= bx0 and ay0 >= by0 and ax1 <= bx1 and ay1 <= by1:
                found.append({"a": a, "relation": "inside", "b": b})
                continue
            shared_x = min(ax1, bx1) - max(ax0, bx0)
            narrower = min(ax1 - ax0, bx1 - bx0) or 1
            if shared_x / narrower >= MIN_SHARED_SPAN and 0 <= ay0 - by1 <= STACK_GAP * H:
                found.append({"a": a, "relation": "below", "b": b})
                continue
            shared_y = min(ay1, by1) - max(ay0, by0)
            shorter = min(ay1 - ay0, by1 - by0) or 1
            if shared_y / shorter >= MIN_SHARED_SPAN and 0 <= ax0 - bx1 <= SIDE_GAP * W:
                found.append({"a": a, "relation": "right_of", "b": b})
    return found[:MAX_RELATIONSHIPS]
