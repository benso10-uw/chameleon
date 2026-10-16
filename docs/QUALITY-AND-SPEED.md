# Quality and speed investigation — 16 October 2026

## Original saved trace

The saved Level 3 trace returned the preset, not either model attempt. Its
total model time was 618.02 seconds (10m18s). Three HTTP 524 responses consumed
378.15 seconds (6m18s). The successful calls generated 28,642 completion tokens,
of which 26,787 (93.5%) were reported as reasoning tokens.

| Stage | Seconds | Reasoning tokens | Result |
| --- | ---: | ---: | --- |
| Understanding | 23.98 | 2,391 | Hard |
| Layout attempt 1 | 121.25 | 13,942 | 9 rule violations |
| Layout attempt 2 | 94.64 | 10,454 | 6 rule violations |
| Three calls for attempt 3 | 378.15 | Not reported | HTTP 524 each |

Levels 1 and 2 spent 18.99 and 32.5 seconds respectively on understanding,
then returned the preset. They never reached model layout generation in those
saved runs. Improving their layout prompt alone could not change those images.

The provider reasoning repeatedly planned all coordinates, discussed groups,
then produced independent group-member geometry that violated those plans.
The reply also lacked reliable font metrics. Long reasoning did not prevent
badge/CTA overflow or broken groups.

## Changes

- Compact banner preset: a broad copy area above a badge/action rail instead of
  squeezing every block into a separate horizontal cell. Level 3's headline
  grows from 6px to approximately 11px; the CTA label grows to about 8.6px.
- The model positions each group with one x/y/scale. Code expands the members
  from their source geometry and enforces a 6px minimum group-label size.
- Non-cover images keep their source ratio inside the model's placement box.
- Prompts include full copy, font information, real measured fit options,
  group size/minimum scale, and explicit composition guidance for banners.
- Workbench skips model understanding and tie-break requests by default.
  Existing engine entry points retain their original routing options.
- Identical previous/best comparisons are reused. Infrastructure failures and
  rate limits stop the request budget instead of immediately retrying the same
  expensive request. Malformed model output still uses the retry budget.
- The model can be asked to refine easy designs; this lets users inspect model
  candidates for Levels 1 and 2 rather than always stopping at the preset.

The workbench's optional deterministic tie-break mode differs from ADR-0001's
LLM tie-break policy. It is a development setting for speed comparisons; the
existing resize defaults retain model judging on ties. It does not override
the rule that hard layouts originate from the model rather than a preset edit.

## Bounded live trials

These are individual development trials, not statistically stable benchmarks.
Times include preview generation and vary with the provider/network.

| Task / model | Setting | Seconds | Outcome |
| --- | --- | ---: | --- |
| Level 3 / Gemini 3.5 Flash-Lite | Minimal | 6.89 | Preset retained |
| Level 3 / Gemini 3.5 Flash-Lite | Low | 5.19 | Preset retained |
| Level 1 / Gemini 3.5 Flash-Lite | Minimal | 5.27 | Preset retained |
| Level 2 / Gemini 3.5 Flash-Lite | Minimal | 6.47 | Preset retained |
| Level 3 / Gemini 3.8 Flash | Low | 91.92 | Request timed out; preset retained |

Flash-Lite reported zero reasoning tokens for the compared Minimal and Low
runs. Thus these measurements do not establish a causal benefit from disabling
reasoning. They demonstrate that a faster model and fewer/smaller calls can be
much quicker. The improved Level 3 preset beat the tested model candidates by
the current rule/signal ranking; no claim is made that the prompt now produces
better-looking outputs than the preset.

Gemini 2.5 Flash appeared in the model list but generation returned HTTP 404:
Google said it was no longer available to new users for this account. The
workbench therefore starts with the current tested Flash-Lite model.

## Visual assessment and remaining limits

- Level 1: acceptable and legible, but its large empty areas make it sparse.
  Model candidates are now inspectable through the refinement option; the
  automated signal checker does not reliably distinguish aesthetic choices.
- Level 2: acceptable, with readable headline/action and visible product. The
  large decorative shapes and badge placement could be more intentional. In
  this trial the model did not beat the preset.
- Level 3: the new preset has a clearer headline and separate action rail,
  retains every element, and passes all rules. Supporting copy/badge labels
  remain small (some at 6px). The task requires over 400 characters, five rigid
  groups and multiple images in 728×90; preserving all content leaves a severe
  readability tradeoff. The holiday asset is intentionally absent in the
  sample and remains a visible placeholder, identified in the UI.

Rules passing is not a visual-quality guarantee. The model judges text/geometry
only in this implementation; it does not inspect rendered images. The UI makes
that gap visible by retaining every candidate for human comparison.

## Recommendation on reasoning

Start with Minimal or Low, two attempts, code-assigned roles, and deterministic
tie-breaks. Compare matched runs of the SAME model and task before changing the
default for quality-sensitive work. Raising reasoning is useful only if it
improves returned layouts enough to justify the latency. Do not treat a tiny
output-token cap as a substitute for controlling reasoning; it can truncate
the JSON and cause retries.

Google documents that Gemini 3 uses thinking levels and cannot guarantee full
thinking-off; Minimal does not guarantee zero thought. Gemini 2.5 Flash supports
a zero thinking budget where available. See
https://ai.google.dev/gemini-api/docs/generate-content/thinking .

## Validation

59 automated tests passed, including existing checker/resize tests and new
group expansion, image proportions, finite numeric inputs, imported-task path
boundaries, credentials excluded from artifacts, cancellation and provider
failure-budget checks. A single-task command-line run produced a real PNG.
Desktop browser checks verified saved trace loading, live stage updates, model
selection, candidate switching, preview loading and a completed Gemini run with
no browser console warnings/errors. Mobile/keyboard/contrast/reduced-motion
checks remain unverified: local browser access was disabled during the final
responsive pass. Screenshot: `output/qa/desktop.png`.
