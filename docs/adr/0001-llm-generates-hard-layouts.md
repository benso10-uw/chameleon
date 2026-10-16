---
status: accepted
---

# LLM generates layouts from scratch for hard designs; deterministic preset logic handles easy ones

The engine routes each design by its difficulty, judged by the LLM from code-measured facts (aspect change, element count, groups, space pressure), with a code override when space pressure is far beyond the preset logic. Easy designs get a preset layout from deterministic logic, which is checked and code-repaired. If it still breaks a rule, the design is promoted to hard. Hard designs are laid out from scratch by the LLM, which reads the element JSON only, inside a check loop. Code produces a violation report each round, with at most 3 design attempts. Code ranks attempts by rule violations, and the LLM weighs signals on ties. We chose this to save time and compute on easy designs, which matters for running locally (stage one's offline container, later an Express add-on), while giving the hardest retargets to the model.

## Considered options

- **LLM proposes structure, code computes every pixel.** Rejected for hard designs: we want the model to own the arrangement where preset logic runs out of ideas.
- **LLM edits the preset layout rather than starting from scratch.** Rejected: a preset layout that failed is not a draft we want to anchor the model to.
- **Classify by the challenge's level.** Not possible: `resize()` is never told the level, so difficulty is the engine's own judgement.

## Consequences

- Hard-design quality depends on a small, local, CPU-only model (amd64 container on Apple hardware, no GPU), so hard designs are slow and less predictable. The model choice waits on the organisers' approved list, and every model call goes through one adapter.
- LLM failures (unparseable, missing or extra ids) are retried with the mistake quoted back. They don't count toward the 3 design attempts but have their own budget: 3 consecutive or 6 total. Fields the LLM may not change (content, font, asset, z-index, group, colours) are silently restored from the source.
- **Accepted risk:** there is no final forced repair. If no LLM attempt beats the preset layout that already failed a rule, that broken layout is returned, and the structural check (e.g. off-canvas) will penalise it.
- Every element must remain present and visible (no dropping, no `opacity: 0`). When space runs out, the order of give is: shrink by priority to a 10px font floor, then allow low-priority elements to overlap background or decoration, then let the lowest-priority text go down to 6px.
