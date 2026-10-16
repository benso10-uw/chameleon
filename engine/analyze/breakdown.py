"""Element breakdown: per-element facts, design measurements, and heuristic
roles/priorities.

The heuristics are what the LLM is shown as hints, and what the engine
falls back on when the LLM can't be used. Priority 1 is the most important
element; priorities are unique.
"""

import math
from dataclasses import dataclass, field
from typing import Any

from engine.analyze.text_metrics import single_line_width

ROLES = ("background", "decoration", "logo", "product", "image", "headline", "subhead",
         "body", "legal", "cta", "badge", "label")

# Lower = more important. Ties are broken by area (bigger first).
_ROLE_RANK = {"headline": 0, "product": 1, "logo": 2, "cta": 3, "subhead": 4, "badge": 5,
              "body": 6, "image": 7, "label": 8, "legal": 9, "decoration": 10, "background": 11}

TEXT_FLOOR = 10.0
LOWEST_TEXT_FLOOR = 6.0
LOW_PRIORITY_ROLES = {"legal"}
LAYERING_ROLES = {"background", "decoration"}


@dataclass
class Understanding:
    roles: dict[str, str]
    priorities: dict[str, int]
    difficulty: str  # "easy" | "hard"
    measurements: dict[str, float] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------

def bbox(el: dict[str, Any]) -> tuple[float, float, float, float]:
    """Axis-aligned bounds (x0, y0, x1, y1) including rotation."""
    x, y, w, h = el["x"], el["y"], el["width"], el["height"]
    angle = math.radians(el.get("rotation", 0) or 0)
    if not angle:
        return x, y, x + w, y + h
    cx, cy = x + w / 2, y + h / 2
    c, s = abs(math.cos(angle)), abs(math.sin(angle))
    hw, hh = (w * c + h * s) / 2, (w * s + h * c) / 2
    return cx - hw, cy - hh, cx + hw, cy + hh


def union(boxes) -> tuple[float, float, float, float]:
    boxes = list(boxes)
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def intersection_area(a, b) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return w * h if w > 0 and h > 0 else 0.0


def groups_of(elements) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for el in elements:
        if el.get("group_id"):
            groups.setdefault(el["group_id"], []).append(el)
    return groups


def text_floor(role: str) -> float:
    return LOWEST_TEXT_FLOOR if role in LOW_PRIORITY_ROLES else TEXT_FLOOR


# ---------------------------------------------------------------------------
# Heuristic roles and priorities
# ---------------------------------------------------------------------------

def infer_roles(source: dict[str, Any]) -> dict[str, str]:
    W, H = source["canvas"]["width"], source["canvas"]["height"]
    canvas_area = W * H
    elements = source["elements"]
    roles: dict[str, str] = {}
    groups = groups_of(elements)

    min_z = min((e.get("z_index", 0) for e in elements), default=0)
    for el in elements:
        area = el["width"] * el["height"]
        if el["type"] == "image" and (el.get("fit") == "cover" or area >= 0.9 * canvas_area) \
                and el.get("z_index", 0) == min_z:
            roles[el["id"]] = "background"
        elif el["type"] == "shape" and not el.get("group_id"):
            x0, y0, x1, y1 = bbox(el)
            off_canvas = x0 < 0 or y0 < 0 or x1 > W or y1 > H
            if el.get("opacity", 1) < 0.5 or off_canvas or el["shape_type"] == "ellipse":
                roles[el["id"]] = "decoration"
            else:
                roles[el["id"]] = "decoration" if area >= 0.6 * canvas_area else "image"

    # Grouped elements: the group with the biggest shape is the CTA; the rest are badges.
    group_area = {gid: sum(e["width"] * e["height"] for e in members if e["type"] == "shape")
                  for gid, members in groups.items()}
    cta_group = max(group_area, key=group_area.get) if group_area else None
    for gid, members in groups.items():
        has_shape = any(m["type"] == "shape" for m in members)
        role = "cta" if gid == cta_group and has_shape else "badge"
        for m in members:
            roles.setdefault(m["id"], role)

    # Images: the topmost wide one in the upper third is the logo; the
    # biggest of the rest is the product.
    images = [e for e in elements if e["type"] == "image" and e["id"] not in roles]
    logos = [e for e in images if e["width"] >= 1.8 * e["height"] and e["y"] < H / 3]
    logo = min(logos, key=lambda e: e["y"]) if logos else None
    rest = [e for e in images if e is not logo]
    product = max(rest, key=lambda e: e["width"] * e["height"]) if rest else None
    for e in images:
        roles[e["id"]] = "logo" if e is logo else "product" if e is product else "image"

    # Text: rank by font size; the smallest long line near the bottom is legal.
    texts = [e for e in elements if e["type"] == "text" and e["id"] not in roles]
    texts.sort(key=lambda e: (-e["font_size"], e["y"]))
    for i, e in enumerate(texts):
        bottom = e["y"] + e["height"] >= 0.85 * H
        small = e["font_size"] <= 0.6 * max(t["font_size"] for t in texts)
        if i == 0:
            roles[e["id"]] = "headline"
        elif bottom and small and len(texts) > 2 and e is texts[-1]:
            roles[e["id"]] = "legal"
        elif i == 1:
            roles[e["id"]] = "subhead"
        else:
            roles[e["id"]] = "body"

    for el in elements:
        roles.setdefault(el["id"], "image" if el["type"] != "text" else "body")
    return roles


def infer_priorities(source: dict[str, Any], roles: dict[str, str]) -> dict[str, int]:
    def key(el):
        rank = _ROLE_RANK.get(roles.get(el["id"], "body"), 6)
        if el["type"] == "text" and rank == _ROLE_RANK["cta"]:
            rank -= 0.5  # a CTA's label outranks its shape
        return rank, -el["width"] * el["height"], el["id"]

    ordered = sorted(source["elements"], key=key)
    return {el["id"]: i + 1 for i, el in enumerate(ordered)}


# ---------------------------------------------------------------------------
# Design measurements
# ---------------------------------------------------------------------------

def measure(source: dict[str, Any], target: dict[str, Any], roles: dict[str, str]) -> dict[str, float]:
    W, H = target["width"], target["height"]
    sw, sh = source["canvas"]["width"], source["canvas"]["height"]
    aspect_change = (W / H) / (sw / sh)
    return {
        "source_aspect": round(sw / sh, 3),
        "target_aspect": round(W / H, 3),
        "aspect_change": round(max(aspect_change, 1 / aspect_change), 3),
        "element_count": len(source["elements"]),
        "group_count": len(groups_of(source["elements"])),
        "space_pressure": round(space_pressure(source, target, roles), 3),
    }


def space_pressure(source: dict[str, Any], target: dict[str, Any], roles: dict[str, str]) -> float:
    """Area the content needs at its minimum readable sizes, over the target
    canvas's area. Above ~1 the content cannot all fit legibly."""
    W, H = target["width"], target["height"]
    short = min(W, H)
    needed = 0.0
    groups = groups_of(source["elements"])
    seen_groups = set()
    for el in source["elements"]:
        role = roles.get(el["id"], "body")
        if role in LAYERING_ROLES:
            continue
        gid = el.get("group_id")
        if gid:
            if gid in seen_groups:
                continue
            seen_groups.add(gid)
            members = groups[gid]
            labels = [m for m in members if m["type"] == "text"]
            scale = max((text_floor(role) / m["font_size"] for m in labels), default=0.2)
            x0, y0, x1, y1 = union(bbox(m) for m in members)
            needed += (x1 - x0) * (y1 - y0) * scale * scale
        elif el["type"] == "text":
            floor = text_floor(role)
            line_h = floor * el.get("line_height", 1.2)
            needed += single_line_width(el, floor) * line_h
        else:
            aspect = el["width"] / max(el["height"], 1)
            min_h = 0.25 * short if role in ("product", "logo") else 0.12 * short
            needed += min_h * min_h * aspect
    return needed / (W * H)
