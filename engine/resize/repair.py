"""Code repair: fixes small rule breaks without asking the LLM.

Small means a few pixels off the canvas, a few percent of image aspect
drift, or text slightly too big for its box. Larger breaks are left for the
check loop to report.
"""

import copy
from typing import Any

from engine.analyze.breakdown import Understanding, bbox, groups_of, text_floor, union
from engine.analyze.text_metrics import measure_text

MAX_SHIFT_FRACTION = 0.03  # of the canvas dimension
MIN_SHIFT_PX = 4.0
MAX_ASPECT_DRIFT = 0.10
MAX_FONT_SHRINK = 0.85  # text may shrink to 85% of its size to fit


def repair(source: dict[str, Any], layout: dict[str, Any], target: dict[str, Any],
           understanding: Understanding) -> dict[str, Any]:
    layout = copy.deepcopy(layout)
    W, H = target["width"], target["height"]
    src_by_id = {el["id"]: el for el in source["elements"]}
    by_id = {el["id"]: el for el in layout["elements"] if el.get("id") in src_by_id}

    # Image aspect drift: restore the source ratio, shrinking about the centre.
    for eid, el in by_id.items():
        src = src_by_id[eid]
        if src["type"] != "image" or src.get("fit") == "cover" or not el.get("height") or not src["height"]:
            continue
        want = src["width"] / src["height"]
        ratio = (el["width"] / el["height"]) / want
        if 1e-9 < abs(ratio - 1) <= MAX_ASPECT_DRIFT:
            cx, cy = el["x"] + el["width"] / 2, el["y"] + el["height"] / 2
            if ratio > 1:
                el["width"] = el["height"] * want
            else:
                el["height"] = el["width"] / want
            el["x"], el["y"] = cx - el["width"] / 2, cy - el["height"] / 2

    # Text slightly too big: grow the box if there's room, else shrink the font a little.
    for eid, el in by_id.items():
        if src_by_id[eid]["type"] != "text" or not isinstance(el.get("font_size"), (int, float)):
            continue
        metrics = measure_text(el)
        if metrics.height > el["height"] and el["y"] + metrics.height <= H and not el.get("group_id"):
            el["height"] = metrics.height
            metrics = measure_text(el)
        floor = text_floor(understanding.roles.get(eid, "body"))
        size = el["font_size"]
        while (metrics.height > el["height"] + 1 or metrics.widest_line > el["width"] + 1) \
                and size * 0.97 >= max(floor, el["font_size"] * MAX_FONT_SHRINK):
            size *= 0.97
            metrics = measure_text(el, font_size=size)
        if size != el["font_size"] and metrics.height <= el["height"] + 1 \
                and metrics.widest_line <= el["width"] + 1:
            el["font_size"] = round(size, 2)

    # Slightly off canvas: shift back in, moving whole groups together.
    units = [[by_id[m["id"]] for m in members if m["id"] in by_id]
             for members in groups_of(source["elements"]).values()]
    grouped = {el["id"] for unit in units for el in unit}
    units += [[el] for eid, el in by_id.items() if eid not in grouped]
    for unit in units:
        if not unit:
            continue
        x0, y0, x1, y1 = union(bbox(el) for el in unit)
        dx = _shift(x0, x1, W, max(MIN_SHIFT_PX, MAX_SHIFT_FRACTION * W))
        dy = _shift(y0, y1, H, max(MIN_SHIFT_PX, MAX_SHIFT_FRACTION * H))
        for el in unit:
            el["x"] += dx
            el["y"] += dy
    return layout


def _shift(lo: float, hi: float, size: float, limit: float) -> float:
    if hi - lo > size:
        return 0.0
    if lo < 0 and -lo <= limit:
        return -lo
    if hi > size and hi - size <= limit:
        return size - hi
    return 0.0
