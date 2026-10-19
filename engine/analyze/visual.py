"""What the analysis model is shown of the design's look.

Design analysis prefers to show the model the rendered source design. When
that can't be rendered or the model refuses images, code extracts the
palette instead (dominant colours of each image asset plus the text and
shape colours); when that also fails, the model works from the element
facts alone. Every step here returns None rather than raising, so the
fallback chain in understand.py decides what happens next.
"""

import io
import re
import tempfile
from pathlib import Path
from typing import Any, Optional

ASSETS_DIR = Path(__file__).resolve().parents[2] / "brand_kit" / "assets"
MAX_IMAGE_SIDE = 1024  # px; enough to read the composition, cheap in image tokens
ASSET_COLOURS = 5
MIN_SHARE = 0.03  # drop colours covering less than this fraction of an asset

_RGBA = re.compile(r"rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*([\d.]+)\s*)?\)")


def render_source_png(source: dict[str, Any], assets_dir: Path = ASSETS_DIR) -> Optional[bytes]:
    """The source design rendered by the real renderer, downscaled to PNG
    bytes, or None if the renderer is missing or fails."""
    try:
        from renderer.converter.convert import render_design_to_png  # type: ignore

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "source.png"
            render_design_to_png(source, assets_dir, str(path))
            return shrink_png(path.read_bytes())
    except Exception:
        return None


def shrink_png(data: bytes) -> Optional[bytes]:
    """A rendered design downscaled to at most MAX_IMAGE_SIDE, as PNG bytes."""
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as image:
            image = image.convert("RGB")
            image.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
            out = io.BytesIO()
            image.save(out, format="PNG", optimize=True)
            return out.getvalue()
    except Exception:
        return None


def extract_palette(source: dict[str, Any], assets_dir: Path = ASSETS_DIR) -> Optional[dict[str, Any]]:
    """Colours code can measure: each image asset's dominant colours with
    their share of its visible pixels, plus every text colour and shape
    fill. None if nothing could be measured."""
    try:
        from PIL import Image
    except ImportError:
        return None
    assets: dict[str, list[dict[str, Any]]] = {}
    for el in source["elements"]:
        asset = el.get("asset_id")
        if el["type"] != "image" or not asset or asset in assets:
            continue
        path = assets_dir / asset
        try:
            with Image.open(path) as image:
                assets[asset] = _dominant(image.convert("RGBA"))
        except Exception:
            continue  # a missing asset still leaves the other colours
    text = _unique(to_hex(el.get("color")) for el in source["elements"] if el["type"] == "text")
    fills = _unique(to_hex(el.get("fill_color")) for el in source["elements"] if el["type"] == "shape")
    if not assets and not text and not fills:
        return None
    return {"image_assets": assets, "text_colours": text, "shape_fills": fills}


def to_hex(colour: Any) -> Optional[str]:
    if not isinstance(colour, str):
        return None
    colour = colour.strip()
    if re.fullmatch(r"#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?", colour):
        return colour[:7].upper()
    match = _RGBA.fullmatch(colour)
    if not match:
        return None
    r, g, b = (max(0, min(255, round(float(v)))) for v in match.groups()[:3])
    return f"#{r:02X}{g:02X}{b:02X}"


def _dominant(image) -> list[dict[str, Any]]:
    image.thumbnail((160, 160))
    raw = image.tobytes()  # RGBA, 4 bytes a pixel
    pixels = [tuple(raw[i:i + 3]) for i in range(0, len(raw), 4) if raw[i + 3] >= 128]
    if not pixels:
        return []
    from PIL import Image
    flat = Image.new("RGB", (len(pixels), 1))
    flat.putdata(pixels)
    quantised = flat.quantize(colors=ASSET_COLOURS)
    palette = quantised.getpalette() or []
    counts = sorted(quantised.getcolors() or [], reverse=True)
    out = []
    for count, index in counts:
        share = count / len(pixels)
        if share < MIN_SHARE:
            continue
        r, g, b = palette[index * 3:index * 3 + 3]
        out.append({"hex": f"#{r:02X}{g:02X}{b:02X}", "share": round(share, 2)})
    return out


def _unique(values) -> list[str]:
    seen: list[str] = []
    for v in values:
        if v and v not in seen:
            seen.append(v)
    return seen
