"""Text measurement that matches what the renderer will draw.

Wrapping reuses the renderer's own glyph-width lookup and greedy word-wrap,
so a font size the engine picks here wraps to the same lines at render time.
If the brand fonts can't be loaded, falls back to a rough per-character
estimate rather than failing.
"""

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

FONTS_DIR = Path(__file__).resolve().parents[2] / "brand_kit" / "fonts"
DEFAULT_LINE_HEIGHT = 1.2
_FALLBACK_CHAR_WIDTH = 0.55  # em, used only when real fonts are unavailable


@dataclass(frozen=True)
class TextMetrics:
    lines: int
    height: float
    widest_line: float


@lru_cache(maxsize=1)
def _renderer_tools():
    try:
        from renderer.converter.brand_fonts import load_brand_font_manager
        from renderer.converter.convert import _normalize_weight, _weight_style_to_font_style
        from renderer.converter.wrap import char_widths, wrap_text

        manager = load_brand_font_manager(str(FONTS_DIR))
        return manager, char_widths, wrap_text, _normalize_weight, _weight_style_to_font_style
    except Exception:
        return None


@lru_cache(maxsize=20000)
def _widths(family: str, weight: str, italic: bool, size: float, text: str) -> tuple[float, ...]:
    tools = _renderer_tools()
    if tools is None:
        return tuple(size * _FALLBACK_CHAR_WIDTH for _ in text)
    manager, char_widths, _, normalize_weight, to_style = tools
    weight = normalize_weight(weight)
    style = to_style(weight == "bold", italic)
    try:
        return tuple(char_widths(manager, family, weight, style, size, text))
    except Exception:
        return tuple(size * _FALLBACK_CHAR_WIDTH for _ in text)


def _wrap(text: str, widths: tuple[float, ...], max_width: float) -> str:
    tools = _renderer_tools()
    if tools is not None:
        return tools[2](text, list(widths), max_width)
    # Same greedy rule as the renderer, for the fallback path.
    chars, line, last_space = list(text), 0.0, None
    for i, c in enumerate(chars):
        if line + widths[i] > max_width and last_space is not None:
            chars[last_space] = "\n"
            line = sum(widths[last_space + 1 : i + 1])
            last_space = None
        else:
            line += widths[i]
        if c == " ":
            last_space = i
    return "".join(chars)


def measure_text(el: dict[str, Any], font_size: Optional[float] = None,
                 width: Optional[float] = None) -> TextMetrics:
    """Measure a text element as wrapped into `width` at `font_size`
    (defaulting to the element's own values)."""
    size = float(font_size if font_size is not None else el["font_size"])
    box_width = float(width if width is not None else el["width"])
    content = str(el.get("content", ""))
    line_height = float(el.get("line_height", DEFAULT_LINE_HEIGHT))
    spacing = float(el.get("letter_spacing", 0.0))
    if not content:
        return TextMetrics(lines=0, height=0.0, widest_line=0.0)

    widths = _widths(el.get("font_family", ""), el.get("font_weight", "regular") or "regular",
                     el.get("font_style") == "italic", round(size, 2), content)
    widths = tuple(w + spacing for w in widths)
    wrapped = _wrap(content, widths, box_width)

    widest, start = 0.0, 0
    lines = wrapped.split("\n")
    for line in lines:
        line_w = sum(widths[start : start + len(line)])
        widest = max(widest, line_w)
        start += len(line) + 1
    return TextMetrics(lines=len(lines), height=len(lines) * size * line_height, widest_line=widest)


def single_line_width(el: dict[str, Any], font_size: float) -> float:
    """Width of the whole content set on one line at `font_size`."""
    return measure_text(el, font_size=font_size, width=float("inf")).widest_line
