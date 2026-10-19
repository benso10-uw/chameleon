"""The extractor: pulls the layout model's reasoning out of its reply.

A layout reply carries geometry plus the model's own account of it: the
pattern it chose, a "why" for each element and group, and a summary of the
new design. Geometry is applied by hard_path._apply; this module keeps the
reasoning so the trace and UI can show it and the next design attempt can
read it back and improve on it. Extraction never rejects a reply: missing
reasoning is noted and asked for next time, not counted as an LLM failure.
"""

from dataclasses import asdict, dataclass, field
from typing import Any

from engine.analyze.breakdown import groups_of

MAX_WHY_CHARS = 400
MAX_SUMMARY_CHARS = 1000


@dataclass
class AttemptRationale:
    pattern: str = ""
    summary: str = ""
    # Ungrouped element id or group id -> the model's justification.
    justifications: dict[str, str] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def extract_rationale(source: dict[str, Any], reply: Any) -> AttemptRationale:
    if not isinstance(reply, dict):
        return AttemptRationale()
    groups = groups_of(source["elements"])
    grouped = {m["id"] for members in groups.values() for m in members}
    expected = [el["id"] for el in source["elements"] if el["id"] not in grouped] + list(groups)
    found: dict[str, str] = {}
    for key, list_name in (("id", "elements"), ("group_id", "groups")):
        for item in reply.get(list_name) or []:
            if isinstance(item, dict) and isinstance(item.get(key), str) and _text(item.get("why")):
                found[item[key]] = item["why"].strip()[:MAX_WHY_CHARS]
    # A reply may list group members individually; their reasons stand for the group.
    for gid, members in groups.items():
        if gid not in found:
            reasons = [found[m["id"]] for m in members if m["id"] in found]
            if reasons:
                found[gid] = reasons[0]
    justifications = {k: found[k] for k in expected if k in found}
    return AttemptRationale(
        pattern=str(reply.get("pattern") or "").strip()[:80],
        summary=str(reply.get("summary") or "").strip()[:MAX_SUMMARY_CHARS],
        justifications=justifications,
        missing=[k for k in expected if k not in justifications])


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())
