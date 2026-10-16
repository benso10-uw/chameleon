"""Preset logic: the deterministic layout.

Content is split into blocks: rigid blocks (images, groups, and rows of
side-by-side elements, which only ever scale uniformly) and text blocks
(whose width follows the space available and whose font size is chosen by
measuring). Blocks are then arranged by the target canvas's shape:

- tall/square: one centred column
- wide: the hero image beside a column of everything else
- strip: one row of cells, with all standalone text sharing one cell

A single scale is binary-searched per column so it fits. Text respects its
readability floor first; only when nothing fits does it go below it.
Background covers the canvas; decorations scale uniformly and are pulled
inside it.
"""

import copy
from dataclasses import dataclass
from typing import Any, Optional

from engine.breakdown import LAYERING_ROLES, Understanding, bbox, text_floor, union
from engine.text_metrics import measure_text

WIDE_ASPECT = 1.4
STRIP_ASPECT = 4.5
HERO_MAX_SHARE = 0.42
SEARCH_STEPS = 40
IMAGE_MIN_SHARE = 0.3  # in a strip, images may shrink to 30% of the height


@dataclass
class Block:
    members: list[dict[str, Any]]
    kind: str  # "rigid" | "text"
    priority: int
    box: tuple[float, float, float, float]  # source bounds, rotation included

    @property
    def width(self) -> float:
        return self.box[2] - self.box[0]

    @property
    def height(self) -> float:
        return self.box[3] - self.box[1]

    @property
    def texts(self) -> list[dict[str, Any]]:
        return [m for m in self.members if m["type"] == "text"]


def preset_layout(source: dict[str, Any], target: dict[str, Any],
                  understanding: Understanding) -> dict[str, Any]:
    W, H = target["width"], target["height"]
    roles, priorities = understanding.roles, understanding.priorities
    placed: dict[str, dict[str, Any]] = {}

    content = [el for el in source["elements"] if roles.get(el["id"]) not in LAYERING_ROLES]
    aspect = W / H
    blocks = _blocks(content, priorities, rows=aspect <= STRIP_ASPECT)
    margin = max(4.0, round(0.05 * min(W, H)))
    gap = max(margin, (0.012 if aspect > STRIP_ASPECT else 0.02) * W)
    area = (margin, margin, W - margin, H - margin)

    if aspect > STRIP_ASPECT:
        _strip(blocks, area, gap, roles, placed, source["canvas"])
    else:
        hero = _hero(blocks, roles) if aspect > WIDE_ASPECT else None
        if hero:
            rest = [b for b in blocks if b is not hero]
            hero_w = min((area[3] - area[1]) * hero.width / hero.height,
                         HERO_MAX_SHARE * (area[2] - area[0]))
            hero_first = _centre_x(hero) <= _centre_x_of(rest, hero)
            if hero_first:
                hero_area = (area[0], area[1], area[0] + hero_w, area[3])
                rest_area = (area[0] + hero_w + gap, area[1], area[2], area[3])
            else:
                hero_area = (area[2] - hero_w, area[1], area[2], area[3])
                rest_area = (area[0], area[1], area[2] - hero_w - gap, area[3])
            _fit_rigid(hero, hero_area, placed, cap=None)
            _column(rest, rest_area, roles, placed, source["canvas"])
        else:
            _column(blocks, area, roles, placed, source["canvas"])

    for el in source["elements"]:
        role = roles.get(el["id"])
        if role == "background":
            placed[el["id"]] = {**el, "x": 0, "y": 0, "width": W, "height": H}
        elif role == "decoration":
            placed[el["id"]] = _decoration(el, source["canvas"], target)

    return {"canvas": {"width": W, "height": H},
            "elements": [placed.get(el["id"], copy.deepcopy(el)) for el in source["elements"]]}


# ---------------------------------------------------------------------------
# Blocks
# ---------------------------------------------------------------------------

def _blocks(content: list[dict[str, Any]], priorities: dict[str, int],
            rows: bool = True) -> list[Block]:
    units: list[list[dict[str, Any]]] = []
    by_group: dict[str, list[dict[str, Any]]] = {}
    for el in content:
        gid = el.get("group_id")
        if gid:
            if gid not in by_group:
                by_group[gid] = []
                units.append(by_group[gid])
            by_group[gid].append(el)
        else:
            units.append([el])

    def make(members, kind=None):
        kind = kind or ("text" if len(members) == 1 and members[0]["type"] == "text" else "rigid")
        return Block(members, kind, min(priorities.get(m["id"], 99) for m in members),
                     union(bbox(m) for m in members))

    blocks = sorted((make(u) for u in units), key=lambda b: (b.box[1], b.box[0]))
    if not rows:
        return blocks

    # Rows: blocks side by side in the source (overlapping vertically, apart
    # horizontally) travel together as one rigid block.
    rows: list[list[Block]] = []
    for b in blocks:
        for row in rows:
            if all(_side_by_side(b, other) for other in row):
                row.append(b)
                break
        else:
            rows.append([b])
    merged = []
    for row in rows:
        if len(row) == 1:
            merged.append(row[0])
        else:
            merged.append(make([m for b in row for m in b.members], kind="rigid"))
    return sorted(merged, key=lambda b: (b.box[1], b.box[0]))


def _side_by_side(a: Block, b: Block) -> bool:
    v_overlap = min(a.box[3], b.box[3]) - max(a.box[1], b.box[1])
    h_apart = a.box[2] <= b.box[0] or b.box[2] <= a.box[0]
    return h_apart and v_overlap > 0.5 * min(a.height, b.height)


def _hero(blocks: list[Block], roles: dict[str, str]) -> Optional[Block]:
    for b in blocks:
        if len(b.members) == 1 and roles.get(b.members[0]["id"]) == "product":
            return b
    return None


def _centre_x(block: Block) -> float:
    return (block.box[0] + block.box[2]) / 2


def _centre_x_of(blocks: list[Block], fallback: Block) -> float:
    if not blocks:
        return _centre_x(fallback)
    box = union(b.box for b in blocks)
    return (box[0] + box[2]) / 2


# ---------------------------------------------------------------------------
# Placing blocks
# ---------------------------------------------------------------------------

def _rigid_scale(block: Block, scale: float, max_w: float, max_h: float,
                 roles: dict[str, str], floor_mode: str) -> float:
    cap = min(max_w / max(block.width, 1e-6), max_h / max(block.height, 1e-6))
    floor_scale = 0.0
    for t in block.texts:
        floor = _floor(t, roles, floor_mode)
        floor_scale = max(floor_scale, floor / t["font_size"])
    return min(max(scale, floor_scale), cap)


def _place_rigid(block: Block, scale: float, x: float, y: float,
                 placed: dict[str, dict[str, Any]]) -> None:
    bx0, by0 = block.box[0], block.box[1]
    for m in block.members:
        out = {**m, "x": x + (m["x"] - bx0) * scale, "y": y + (m["y"] - by0) * scale,
               "width": m["width"] * scale, "height": m["height"] * scale}
        if m["type"] == "text":
            out["font_size"] = m["font_size"] * scale
        placed[m["id"]] = _rounded(out)


def _fit_rigid(block: Block, area, placed, cap: Optional[float] = 1.0) -> float:
    aw, ah = area[2] - area[0], area[3] - area[1]
    scale = min(aw / block.width, ah / block.height)
    if cap is not None:
        scale = min(scale, cap)
    w, h = block.width * scale, block.height * scale
    _place_rigid(block, scale, area[0] + (aw - w) / 2, area[1] + (ah - h) / 2, placed)
    return scale


def _floor(text_el: dict[str, Any], roles: dict[str, str], floor_mode: str) -> float:
    if floor_mode == "none":
        return 1.0
    if floor_mode == "low":
        return 6.0
    return text_floor(roles.get(text_el["id"], "body"))


def _text_size(el, scale, roles, floor_mode) -> float:
    return max(el["font_size"] * scale, _floor(el, roles, floor_mode))


def _column(blocks: list[Block], area, roles, placed, src_canvas) -> None:
    """Stack blocks top to bottom in source order, centred in `area`, at the
    largest scale that fits."""
    if not blocks:
        return
    aw, ah = area[2] - area[0], area[3] - area[1]
    gap = 0.04 * ah

    def heights(scale, floor_mode):
        out = []
        for b in blocks:
            if b.kind == "text":
                el = b.members[0]
                size = _text_size(el, scale, roles, floor_mode)
                out.append(measure_text(el, font_size=size, width=aw).height)
            else:
                out.append(b.height * _rigid_scale(b, scale, aw, ah, roles, floor_mode))
        return out

    def fits(scale, floor_mode):
        return _column_fits(blocks, aw, ah, roles, scale, floor_mode)

    max_scale = max(aw / src_canvas["width"], ah / src_canvas["height"]) * 1.0
    scale, mode = None, "normal"
    for floor_mode in ("normal", "low", "none"):
        if fits(1e-3, floor_mode):
            mode = floor_mode
            scale = _search(lambda s: fits(s, floor_mode), 1e-3, max_scale)
            break
    if scale is None:
        scale, mode = 1e-3, "none"

    hs = heights(scale, mode)
    spacing = _spacing(blocks, hs, ah, gap, src_canvas["height"])
    y = area[1] + spacing[0]
    src_cx = src_canvas["width"] / 2
    for i, (b, h) in enumerate(zip(blocks, hs)):
        if b.kind == "text":
            el = b.members[0]
            size = _text_size(el, scale, roles, mode)
            placed[el["id"]] = _rounded({**el, "x": area[0], "y": y, "width": aw,
                                         "height": h, "font_size": size})
        else:
            s = _rigid_scale(b, scale, aw, ah, roles, mode)
            w = b.width * s
            x = _align_x(b, w, area, src_canvas["width"], src_cx)
            _place_rigid(b, s, x, y, placed)
        y += h + spacing[i + 1]


def _spacing(blocks: list[Block], heights: list[float], avail: float, gap: float,
             src_h: float) -> list[float]:
    """Space above, between, and below the stacked blocks. The minimum gap
    goes between blocks; leftover room is shared out in proportion to the
    source's own gaps, so a logo that sat at the top stays near the top."""
    n = len(blocks)
    src_gaps = [max(0.0, blocks[0].box[1])]
    src_gaps += [max(0.0, blocks[i].box[1] - blocks[i - 1].box[3]) for i in range(1, n)]
    src_gaps.append(max(0.0, src_h - blocks[-1].box[3]))
    minimum = [0.0] + [gap] * (n - 1) + [0.0]
    free = avail - sum(heights) - gap * (n - 1)
    if free <= 0:
        return minimum
    total = sum(src_gaps)
    if total <= 0:
        return [free / 2] + [gap] * (n - 1) + [free / 2]
    return [m + free * g / total for m, g in zip(minimum, src_gaps)]


def _align_x(block: Block, w: float, area, src_w: float, src_cx: float) -> float:
    """Keep a block's horizontal alignment from the source: hugging the left
    or right edge stays there, anything else is centred."""
    left, right = block.box[0] / src_w, 1 - block.box[2] / src_w
    if left < 0.12 and right > 0.3:
        return area[0]
    if right < 0.12 and left > 0.3:
        return area[2] - w
    return area[0] + (area[2] - area[0] - w) / 2


def _strip(blocks: list[Block], area, gap, roles, placed, src_canvas) -> None:
    """One row of cells across a very wide, short canvas. Each rigid block
    gets a cell fitted to the height; neighbouring badges share a cell,
    stacked; all standalone text shares one cell, given the width it needs.
    Rigid cells squeeze (towards their readability floor) until the text
    fits; floors relax only when nothing else works."""
    aw, ah = area[2] - area[0], area[3] - area[1]
    texts = sorted((b for b in blocks if b.kind == "text"), key=lambda b: (b.box[1], b.box[0]))
    rigid = sorted((b for b in blocks if b.kind == "rigid"), key=lambda b: (b.box[1] + b.box[3]) / 2)

    # Neighbouring badges stack into one cell.
    cells: list[list[Block]] = []
    for b in rigid:
        is_badge = all(roles.get(m["id"]) == "badge" for m in b.members)
        prev = cells[-1] if cells else None
        if is_badge and prev and all(roles.get(m["id"]) == "badge" for pb in prev for m in pb.members)                 and not any(m.get("rotation") for pb in prev + [b] for m in pb.members):
            prev.append(b)
        else:
            cells.append([b])

    inner_gap = 0.06 * ah
    order: list[Any] = list(cells)
    if texts:
        lead = min(texts, key=lambda b: b.priority)
        lead_y = (lead.box[1] + lead.box[3]) / 2
        idx = sum(1 for c in cells if (c[0].box[1] + c[0].box[3]) / 2 < lead_y)
        order.insert(idx, "text")
    gaps = gap * (len(order) - 1)

    def cell_size(cell, s):
        return (max(b.width for b in cell) * s,
                sum(b.height for b in cell) * s + inner_gap * (len(cell) - 1))

    def cell_range(cell, floor_mode):
        stack_h = sum(b.height for b in cell)
        s_max = min(1.0, (ah - inner_gap * (len(cell) - 1)) / stack_h)
        floors = [_floor(t, roles, floor_mode) / t["font_size"] for b in cell for t in b.texts]
        s_min = max(floors) if floors else IMAGE_MIN_SHARE * s_max
        return s_min, s_max

    for floor_mode in ("normal", "low", "none"):
        ranges = [cell_range(c, floor_mode) for c in cells]
        if any(lo > hi + 1e-9 for lo, hi in ranges):
            continue
        text_w = _column_min_width(texts, ah, roles, floor_mode) if texts else 0.0

        # Squeeze lowest-priority cells first: as u falls from n to 0, cell k
        # (0 = most important) shrinks once u drops below k + 1.
        rank = {id(c): k for k, c in enumerate(sorted(cells, key=lambda c: min(b.priority for b in c)))}

        def scales_at(u):
            return [lo + min(max(u - rank[id(c)], 0.0), 1.0) * (hi - lo)
                    for c, (lo, hi) in zip(cells, ranges)]

        def total(u):
            return sum(cell_size(c, sc)[0] for c, sc in zip(cells, scales_at(u)))

        if total(0.0) + gaps + text_w > aw:
            continue
        u = _search(lambda u: total(u) + gaps + text_w <= aw, 0.0, float(len(cells)))
        scales = scales_at(u)
        break
    else:  # nothing fits even with no floors: squeeze everything in proportion
        floor_mode = "none"
        scales = [hi for _, hi in (cell_range(c, "none") for c in cells)]
        text_w = 0.25 * aw if texts else 0.0
        room = max(aw - gaps - text_w, 0.0)
        widths = sum(cell_size(c, s)[0] for c, s in zip(cells, scales))
        if widths > room:
            scales = [s * room / widths for s in scales]

    rigid_w = sum(cell_size(c, s)[0] for c, s in zip(cells, scales))
    text_w = aw - gaps - rigid_w
    scale_of = {id(c): s for c, s in zip(cells, scales)}
    x = area[0]
    for cell in order:
        if cell == "text":
            _column(texts, (x, area[1], x + text_w, area[3]), roles, placed, src_canvas)
            x += text_w + gap
            continue
        s = scale_of[id(cell)]
        w, h = cell_size(cell, s)
        y = area[1] + (ah - h) / 2
        for b in cell:
            _place_rigid(b, s, x + (w - b.width * s) / 2, y, placed)
            y += b.height * s + inner_gap
        x += w + gap


def _column_fits(blocks: list[Block], aw: float, ah: float, roles, scale: float,
                 floor_mode: str) -> bool:
    gap = 0.04 * ah
    total = gap * (len(blocks) - 1)
    for b in blocks:
        if b.kind == "text":
            el = b.members[0]
            total += measure_text(el, font_size=_text_size(el, scale, roles, floor_mode),
                                  width=aw).height
        else:
            total += b.height * _rigid_scale(b, scale, aw, ah, roles, floor_mode)
    return total <= ah


def _column_min_width(blocks: list[Block], ah: float, roles, floor_mode: str) -> float:
    """Narrowest column in which `blocks` fit at their floors."""
    lo, hi = 1.0, 4000.0
    if not _column_fits(blocks, hi, ah, roles, 1e-3, floor_mode):
        return hi
    for _ in range(SEARCH_STEPS):
        mid = (lo + hi) / 2
        if _column_fits(blocks, mid, ah, roles, 1e-3, floor_mode):
            hi = mid
        else:
            lo = mid
    return hi * 1.02


def _search(ok, lo: float, hi: float) -> float:
    """Largest value in [lo, hi] for which `ok` holds, given ok(lo)."""
    if ok(hi):
        return hi
    for _ in range(SEARCH_STEPS):
        mid = (lo + hi) / 2
        if ok(mid):
            lo = mid
        else:
            hi = mid
    return lo


def _decoration(el: dict[str, Any], src_canvas: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    W, H = target["width"], target["height"]
    sW, sH = src_canvas["width"], src_canvas["height"]
    scale = ((W * H) / (sW * sH)) ** 0.5
    x0, y0, x1, y1 = bbox(el)
    rot_w, rot_h = x1 - x0, y1 - y0
    scale = min(scale, W / max(rot_w, 1e-6), H / max(rot_h, 1e-6))
    w, h = el["width"] * scale, el["height"] * scale
    cx = (x0 + x1) / 2 / sW * W
    cy = (y0 + y1) / 2 / sH * H
    half_w, half_h = rot_w * scale / 2, rot_h * scale / 2
    cx = min(max(cx, half_w), W - half_w)
    cy = min(max(cy, half_h), H - half_h)
    return _rounded({**el, "x": cx - w / 2, "y": cy - h / 2, "width": w, "height": h})


def _rounded(el: dict[str, Any]) -> dict[str, Any]:
    for k in ("x", "y", "width", "height", "font_size"):
        if k in el:
            el[k] = round(float(el[k]), 2)
    return el
