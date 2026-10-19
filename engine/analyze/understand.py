"""Understanding: design analysis, roles, priorities and difficulty.

Code breaks the design down and measures it. One design-analysis call then
asks the LLM how the design works (colours, composition pattern, a short
description, the relationships that must survive) and to assign each
element a role and priority and judge the design easy or hard. Code adds
the technical facts it measures exactly.

The model is shown the rendered source design when it accepts images. If
the design can't be rendered or the model refuses the image, it gets the
palette code extracts instead, and failing that the element facts alone.
A code override forces hard when space pressure is far beyond what the
preset logic can handle. If the LLM can't be used, the heuristics stand.
"""

import json
from pathlib import Path
from typing import Any, Optional

from engine.analyze.analysis import (PATTERNS, RELATIONS, SCHEMES, DesignAnalysis, infer_relationships,
                                     technical_facts)
from engine.analyze.breakdown import (ROLES, Understanding, infer_priorities, infer_roles, measure)
from engine.analyze.visual import extract_palette, render_source_png
from engine.model.llm import LLMBudget, UnusableReply, ask_json, render_prompt
from engine.model.model import ImagesUnsupported, ModelAdapter, NoModel
from engine.tracing.trace import record

PROMPTS = Path(__file__).parent / "prompts"
HARD_PRESSURE = 0.6
HEURISTIC_HARD_ASPECT_CHANGE = 2.0
HEURISTIC_HARD_PRESSURE = 0.35
_TEXT_PREVIEW = 160

VISUAL_INPUT = {
    "image": ("The rendered source design is attached as an image. Read colours, imagery and mood "
              "from it; take exact positions and sizes from the element list below."),
    "palette": ("No image of the design is available. Colours measured in code (each image asset's "
                "dominant colours with their share of its pixels, then text colours and shape fills):\n"
                "${colours}\nJudge imagery from asset file names and these colours."),
    "text": ("No image or measured colours are available. Infer colours from the elements' color and "
             "fill_color fields and asset file names, and say in colors.notes that the palette is inferred."),
}


def understand(source: dict[str, Any], target: dict[str, Any], model: ModelAdapter,
               budget: LLMBudget, use_model: bool = True,
               source_image: Optional[bytes] = None) -> Understanding:
    roles = infer_roles(source)
    priorities = infer_priorities(source, roles)
    measurements = measure(source, target, roles)
    hint_relationships = infer_relationships(source, roles)
    ids = [el["id"] for el in source["elements"]]

    def parse(reply: Any) -> Understanding:
        if not isinstance(reply, dict) or not isinstance(reply.get("elements"), list):
            raise UnusableReply('expected {"colors": ..., "pattern": ..., "description": ..., '
                                '"elements": [...], "relationships": [...], "difficulty": ...}')
        got: dict[str, dict[str, Any]] = {}
        for item in reply["elements"]:
            if not isinstance(item, dict) or "id" not in item:
                raise UnusableReply("every entry in elements needs an id")
            got[str(item["id"])] = item
        missing, extra = set(ids) - set(got), set(got) - set(ids)
        if missing or extra:
            raise UnusableReply(f"element ids must match exactly; missing {sorted(missing)}, "
                                f"unknown {sorted(extra)}")
        bad_roles = sorted({str(got[i].get("role")) for i in ids} - set(ROLES))
        if bad_roles:
            raise UnusableReply(f"unknown roles {bad_roles}; use only {list(ROLES)}")
        for i in ids:
            if not isinstance(got[i].get("priority"), (int, float)):
                raise UnusableReply(f"element '{i}' needs a numeric priority")
        difficulty = reply.get("difficulty")
        if difficulty not in ("easy", "hard"):
            raise UnusableReply('difficulty must be "easy" or "hard"')
        colors, pattern = reply.get("colors"), reply.get("pattern")
        if not isinstance(colors, dict) or not isinstance(colors.get("palette"), list) \
                or not _text(colors.get("scheme")):
            raise UnusableReply('colors needs {"palette": [{"hex", "name", "usage"}], "scheme", "notes"}')
        if not isinstance(pattern, dict) or not _text(pattern.get("source")) or not _text(pattern.get("target")):
            raise UnusableReply('pattern needs {"source", "source_notes", "target", "target_notes"}')
        if not _text(reply.get("description")):
            raise UnusableReply("description must be 3-4 sentences on how the design works")
        llm_roles = {i: got[i]["role"] for i in ids}
        # Unique ranks 1..N; heuristic order breaks ties.
        ranked = sorted(ids, key=lambda i: (got[i]["priority"], priorities[i]))
        llm_priorities = {i: n + 1 for n, i in enumerate(ranked)}
        relationships = [r for r in reply.get("relationships") or []
                         if isinstance(r, dict) and r.get("a") and r.get("b") and r.get("relation")]
        analysis = DesignAnalysis(
            visual_input="", decided_by="llm",
            palette=[p for p in colors["palette"] if isinstance(p, dict)],
            color_scheme=str(colors["scheme"]).strip().lower().replace(" ", "_").replace("-", "_"),
            color_notes=str(colors.get("notes") or ""),
            pattern=str(pattern["source"]), pattern_notes=str(pattern.get("source_notes") or ""),
            target_pattern=str(pattern["target"]), target_pattern_notes=str(pattern.get("target_notes") or ""),
            description=str(reply["description"]).strip(),
            priority_reasons={i: str(got[i].get("why") or "") for i in ids},
            relationships=relationships or hint_relationships,
            technical=technical_facts(source, llm_roles, llm_priorities))
        return Understanding(llm_roles, llm_priorities, difficulty, measurements, analysis)

    result, visual_input, colours = None, "none", None
    if use_model and not isinstance(model, NoModel):
        result, visual_input, colours = _ask(source, target, model, budget, parse, source_image,
                                             roles, priorities, measurements, hint_relationships)
    decided_by = "llm" if result is not None else "heuristics"
    if result is None:
        hard = (measurements["aspect_change"] > HEURISTIC_HARD_ASPECT_CHANGE
                or measurements["space_pressure"] > HEURISTIC_HARD_PRESSURE)
        if colours is None and not isinstance(model, NoModel):
            colours = extract_palette(source)  # still useful to the layout prompt
        analysis = DesignAnalysis(visual_input="none", decided_by="heuristics",
                                  relationships=hint_relationships,
                                  technical=technical_facts(source, roles, priorities))
        result = Understanding(roles, priorities, "hard" if hard else "easy", measurements, analysis)
    result.analysis.visual_input = visual_input if decided_by == "llm" else "none"
    result.analysis.extracted_colours = colours
    judged = result.difficulty
    if measurements["space_pressure"] > HARD_PRESSURE:
        result.difficulty = "hard"
    record("understand", decided_by=decided_by, measurements=measurements,
           heuristic_roles=roles, heuristic_priorities=priorities,
           roles=result.roles, priorities=result.priorities,
           judged_difficulty=judged, difficulty=result.difficulty,
           pressure_override=judged != result.difficulty,
           analysis=result.analysis.to_dict())
    return result


def _ask(source, target, model, budget, parse, source_image, roles, priorities, measurements,
         relationships) -> tuple[Optional[Understanding], str, Optional[dict]]:
    """The analysis call, walking down image -> extracted palette -> text only."""
    def prompt(mode, colours=None):
        return _prompt(source, target, roles, priorities, measurements, relationships, mode, colours)

    if getattr(model, "accepts_images", False):
        image = source_image or render_source_png(source)
        if image is None:
            record("visual_input", mode="image", used=False,
                   reason="the source design could not be rendered; using extracted colours")
        else:
            try:
                result = ask_json(model, prompt("image"), parse, budget, purpose="understand",
                                  images=[image])
                _record_outcome("image", result, image_bytes=len(image))
                return result, "image", None
            except ImagesUnsupported as exc:
                record("visual_input", mode="image", used=False,
                       reason=f"{exc}; using extracted colours")
    colours = extract_palette(source)
    mode = "palette" if colours else "text"
    if colours is None:
        record("visual_input", mode="palette", used=False,
               reason="no colours could be extracted; using element facts only")
    result = ask_json(model, prompt(mode, colours), parse, budget, purpose="understand")
    _record_outcome(mode, result)
    return result, mode, colours


def _record_outcome(mode: str, result: Optional[Understanding], **extra: Any) -> None:
    if result is not None:
        record("visual_input", mode=mode, used=True, **extra)
    else:
        record("visual_input", mode=mode, used=False,
               reason="the analysis request produced no usable reply; code heuristics stand")


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def element_facts(source: dict[str, Any]) -> list[dict[str, Any]]:
    facts = []
    for el in source["elements"]:
        fact = {k: el[k] for k in ("id", "type", "group_id", "z_index", "rotation", "opacity",
                                   "font_size", "font_weight", "asset_id", "fit", "shape_type",
                                   "color", "fill_color")
                if el.get(k) is not None}
        fact["box"] = [round(el[k], 1) for k in ("x", "y", "width", "height")]
        if el["type"] == "text":
            content = el.get("content", "")
            fact["text"] = content if len(content) <= _TEXT_PREVIEW else content[:_TEXT_PREVIEW] + "…"
            fact["text_length"] = len(content)
        facts.append(fact)
    return facts


def _prompt(source, target, roles, priorities, measurements, relationships, mode, colours) -> str:
    facts = element_facts(source)
    for f in facts:
        f["hint_role"], f["hint_priority"] = roles[f["id"]], priorities[f["id"]]
    visual = VISUAL_INPUT[mode].replace("${colours}", json.dumps(colours) if colours else "")
    return render_prompt(PROMPTS / "understand.md",
                         visual_input=visual,
                         source_width=source["canvas"]["width"],
                         source_height=source["canvas"]["height"],
                         target_width=target["width"], target_height=target["height"],
                         measurements=json.dumps(measurements),
                         facts=json.dumps(facts, ensure_ascii=False),
                         relationships=json.dumps(relationships, ensure_ascii=False),
                         roles=list(ROLES), patterns=list(PATTERNS), schemes=list(SCHEMES),
                         relations=list(RELATIONS))
