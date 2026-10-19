# Chameleon

An engine that retargets a finished design to a new canvas size by rearranging its elements, not stretching them, so the result still looks designed.

## Language

### Designs

**Source design**:
The design as received, at its original canvas size: a canvas plus its list of elements.
_Avoid_: input, original poster

**Target canvas**:
The width and height the source design must be retargeted into.
_Avoid_: new ratio, target size

**Element**:
A single text, image, or shape in a design, identified by a stable id that must survive retargeting exactly once.
_Avoid_: item, object, component

**Group**:
Elements sharing a `group_id`; a designer-intended unit (e.g. a button's shape plus its label) that moves as one.
_Avoid_: unit, cluster

**Level**:
The challenge's difficulty rating of a task (warm-up, core, advanced). Describes the input, never the engine's internal routing.
_Avoid_: difficulty (when meaning the challenge's rating)

**Difficulty**:
The engine's own judgement, made during understanding, of how hard a source design is to retarget to a given target canvas; it decides which path produces the layout.
_Avoid_: level

### Understanding

**Role**:
What an element is for in the design (headline, logo, product, CTA, decoration, background, legal…), inferred because designs carry no semantic labels.
_Avoid_: label, category, type (type is text/image/shape)

**Priority**:
How much an element matters to the design's message; decides who gets space when the target canvas is tight. Headlines, QR codes and dates rank above other content.
_Avoid_: importance, weight

**Design analysis**:
The structured account of how a source design works: palette and colour scheme, composition pattern (and the one suggested for the target canvas), a short description, the reason behind each priority, relationships between elements, and code-measured technical facts. Made by one LLM call that is shown the rendered design when possible.
_Avoid_: understanding (the wider step), breakdown (the code facts)

**Composition pattern**:
How a design arranges visual weight and directs the eye (centred, asymmetrical focal, Z-pattern, split-screen…). Layouts adapt the pattern to the target canvas rather than copy coordinates.
_Avoid_: design pattern, template

### Producing

**Layout**:
A complete assignment of position, size (and font size for text) to every element for the target canvas.
_Avoid_: formation, arrangement

### Checking

**Rule**:
A requirement every returned layout must satisfy: every element present exactly once and still visible, required fields intact, images in proportion, groups intact, and text fitting its box at 6px or more.
_Avoid_: hard check, constraint

**Signal**:
A quality observation about a layout (an element reaching off the target canvas, unintended overlap, readability, alignment, prominence, space use) that informs judgement but never rejects a layout on its own.
_Avoid_: score, soft check

**Preset layout**:
The layout produced by the engine's deterministic logic; it is the layout for easy designs and the safety net returned when no LLM attempt beats it.
_Avoid_: fallback layout, baseline

**Space pressure**:
How much room the elements need at their minimum readable sizes compared with the room the target canvas offers.
_Avoid_: empty space, fit ratio

**Violation report**:
The per-element account of how a layout breaks rules or raises signals: what broke, how severely, how far the element moved, and its priority.
_Avoid_: error list, feedback

**Check loop**:
The cycle of checking a layout, producing its violation report, and asking the LLM for an improved attempt, until attempts stop improving or the attempt cap is reached.
_Avoid_: retry loop, evaluation loop

**Design attempt**:
One usable layout returned by the LLM within the check loop; counts toward the attempt cap.
_Avoid_: iteration, try

**Rationale**:
The LLM's own account of a design attempt, extracted from its reply: the pattern it used, a justification for each element and group, and a summary of the new design. The next attempt reads it back to improve on it.
_Avoid_: explanation, notes

**LLM failure**:
A reply that cannot be used as a layout at all (unparseable, or missing/extra element ids); retried with the mistake quoted back, and counted against its own budget rather than the attempt cap.
_Avoid_: failed attempt, design failure
