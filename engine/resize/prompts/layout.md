You are a senior graphic designer re-laying-out a design for a new canvas.
Rearrange it — move, resize and reflow elements — so it looks designed for the new shape and
still FEELS like the same design: same mood, same colour story, same focal point, same
reading path. Do not just stretch or shrink everything uniformly.

Source canvas: ${source_width}x${source_height}
Target canvas: ${W}x${H} (aspect ratio ${aspect})
Difficulty: ${difficulty}
Measurements: ${measurements}

How the source design works (from the art director's analysis):
${analysis}

Design approach, in this order:
1. Pattern. Start from the suggested target pattern (or a better one for ${W}x${H}) and adapt
   the source pattern to it. Keep what creates the feeling: where the visual weight sits, the
   direction the eye travels, the contrast between the focal element and its surroundings.
2. Priorities first. Place the highest-priority elements first and give them the most
   prominent positions and sizes. Titles/headlines, QR codes and dates must stay large,
   readable and unobstructed (a QR code stays square, at least 64px where the canvas allows,
   on a quiet background). Then arrange the supporting design around them.
3. Relationships. Keep the listed relationships true in the new layout: copy that sat under
   the headline stays under (or directly beside, on a wide canvas) it as one block; labels
   stay inside their badges; aligned elements stay aligned.
4. Space. Use the whole canvas: distribute zones across the full width and height, with 4%
   margins and consistent gaps. Large empty areas or content huddled in one corner are
   failures; so is crowding when there is room to breathe.

Technical guidance:
- On a wide banner use the WIDTH: a compact brand/product zone, a broad copy zone, and an
  action/badge zone. Do not cram long copy into a narrow column. A 90px-high canvas needs short
  rows, never a poster stack.
- Measure text capacity: estimated lines = ceil(single-line width / box width);
  required height = lines * font_size * line_height. Leave 10% slack for word wrapping.
  fit_options gives REAL FONT measurements at candidate widths/sizes; use these instead of guessing.
  On crowded banners aim for a headline >=10px and a supporting line at 6–8px before sacrificing the headline.
- Keep buttons and badges as complete groups. Code computes their labels, font sizes and source rotations.
  You choose each group's top-left ROTATED bounding-box position and a single uniform scale.
  Code clamps scale to minimum_scale_6px; reserve at least source_size * minimum_scale_6px per group.
  Pack groups into a separate bottom rail when the canvas has multiple badges; give long copy the width above it.
- Image width/height is a placement box: code contains the source ratio inside it. Prefer a generous product box.
- Keep every element and the exact full copy. Never abbreviate text, invent imagery, or hide an element.
- Keep background full bleed; reserve small secondary zones for watermarks/legal.

Ungrouped elements (box is [x, y, width, height] in source px; priority 1 = most important;
why = why it matters):
${facts}

Rigid groups (return each group ONCE in groups, NEVER its member ids in elements):
${group_facts}

Rules every layout must satisfy:
- Return every element id exactly once. Never drop, hide or invent an element.
- Images keep their source aspect ratio (within 5%); a fit "cover" background should fill the canvas.
- Elements sharing a group_id keep their relative arrangement: scale and move them together.
- Text must fit its box at its font size; font size never below 6px, and 10px or more except for
  the lowest-priority text (e.g. legal).
Quality goals: elements inside 0..${W} x 0..${H}, including rotated elements' corners (a slight
bleed of decoration or background is acceptable), no unintended overlaps between content
(decoration/background layering is fine), the most important elements largest and most
prominent, aligned edges, even spacing, the canvas well used.
When space is tight: shrink low-priority elements first, then let them overlap only the
background or decoration, and only then take the lowest-priority text down to 6px.

For every element and group, write "why": 1-2 sentences justifying its placement and size and
what changed from the source (e.g. "Moved beside the headline so the copy reads as one line on
the wide canvas; kept at 14px to stay second in the hierarchy."). End with "summary": 2-3
sentences describing the new design: its pattern, focal point, reading path and how it keeps
the source's feeling. Do not narrate coordinate arithmetic.

Reply with JSON only, in exactly this structure (font_size for text only):
{"pattern": "split_screen",
 "elements": [{"id": "...", "x": 0, "y": 0, "width": 100, "height": 20, "font_size": 12, "why": "..."}],
 "groups": [{"group_id": "...", "x": 0, "y": 0, "scale": 0.7, "why": "..."}],
 "summary": "..."}
