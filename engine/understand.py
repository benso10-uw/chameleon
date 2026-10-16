"""Understanding: roles, priorities and difficulty for a source design.

Code breaks the design down and measures it; the LLM reads that JSON and
assigns each element a role and priority and judges the design easy or
hard. A code override forces hard when space pressure is far beyond what
the preset logic can handle. If the LLM can't be used, the heuristics stand.
"""

import json
from typing import Any

from engine.breakdown import (ROLES, Understanding, infer_priorities, infer_roles, measure)
from engine.llm import LLMBudget, UnusableReply, ask_json
from engine.model import ModelAdapter
from engine.trace import record

HARD_PRESSURE = 0.6
HEURISTIC_HARD_ASPECT_CHANGE = 2.0
HEURISTIC_HARD_PRESSURE = 0.35
_TEXT_PREVIEW = 160


def understand(source: dict[str, Any], target: dict[str, Any], model: ModelAdapter,
               budget: LLMBudget) -> Understanding:
    roles = infer_roles(source)
    priorities = infer_priorities(source, roles)
    measurements = measure(source, target, roles)
    ids = [el["id"] for el in source["elements"]]

    def parse(reply: Any) -> Understanding:
        if not isinstance(reply, dict) or not isinstance(reply.get("elements"), list):
            raise UnusableReply('expected {"elements": [...], "difficulty": ...}')
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
        llm_roles = {i: got[i]["role"] for i in ids}
        # Unique ranks 1..N; heuristic order breaks ties.
        ranked = sorted(ids, key=lambda i: (got[i]["priority"], priorities[i]))
        return Understanding(llm_roles, {i: n + 1 for n, i in enumerate(ranked)},
                             difficulty, measurements)

    result = ask_json(model, _prompt(source, target, roles, priorities, measurements), parse, budget,
                      purpose="understand")
    decided_by = "llm" if result is not None else "heuristics"
    if result is None:
        hard = (measurements["aspect_change"] > HEURISTIC_HARD_ASPECT_CHANGE
                or measurements["space_pressure"] > HEURISTIC_HARD_PRESSURE)
        result = Understanding(roles, priorities, "hard" if hard else "easy", measurements)
    judged = result.difficulty
    if measurements["space_pressure"] > HARD_PRESSURE:
        result.difficulty = "hard"
    record("understand", decided_by=decided_by, measurements=measurements,
           heuristic_roles=roles, heuristic_priorities=priorities,
           roles=result.roles, priorities=result.priorities,
           judged_difficulty=judged, difficulty=result.difficulty,
           pressure_override=judged != result.difficulty)
    return result


def element_facts(source: dict[str, Any]) -> list[dict[str, Any]]:
    facts = []
    for el in source["elements"]:
        fact = {k: el[k] for k in ("id", "type", "group_id", "z_index", "rotation", "opacity",
                                   "font_size", "font_weight", "asset_id", "fit", "shape_type")
                if el.get(k) is not None}
        fact["box"] = [round(el[k], 1) for k in ("x", "y", "width", "height")]
        if el["type"] == "text":
            content = el.get("content", "")
            fact["text"] = content if len(content) <= _TEXT_PREVIEW else content[:_TEXT_PREVIEW] + "…"
            fact["text_length"] = len(content)
        facts.append(fact)
    return facts


def _prompt(source, target, roles, priorities, measurements) -> str:
    facts = element_facts(source)
    for f in facts:
        f["hint_role"], f["hint_priority"] = roles[f["id"]], priorities[f["id"]]
    return f"""You are analysing a graphic design that must be re-laid-out for a new canvas size.
The design has no semantic labels: work out what each element is from its type, box
[x, y, width, height] in px, text, font size and group. Elements sharing a group_id are
one designer-intended unit (for example a button shape and its label).

Source canvas: {source["canvas"]["width"]}x{source["canvas"]["height"]}
Target canvas: {target["width"]}x{target["height"]}
Measurements (computed in code): {json.dumps(measurements)}
space_pressure is the area the content needs at minimum readable size divided by the
target canvas area; above 1 it cannot all fit legibly.

Elements (hint_role and hint_priority are a heuristic guess you may override):
{json.dumps(facts, ensure_ascii=False)}

For every element give:
- role: one of {list(ROLES)}
- priority: 1 = most important to the design's message, larger = less important
Then judge difficulty: "easy" if moving and uniformly resizing elements will clearly fit the
target canvas well, "hard" if the shape change or space pressure needs real rearrangement.

Reply with JSON only:
{{"elements": [{{"id": "...", "role": "...", "priority": 1}}, ...], "difficulty": "easy" | "hard"}}"""
