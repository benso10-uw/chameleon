You are an art director analysing a finished graphic design before it is re-laid-out for a
new canvas size. Nothing is being moved yet: your job is to understand how the design works,
so the layout designer can keep its feeling in a different shape.

${visual_input}

Source canvas: ${source_width}x${source_height}
Target canvas: ${target_width}x${target_height}
Measurements (computed in code): ${measurements}
space_pressure is the area the content needs at minimum readable size divided by the
target canvas area; above 1 it cannot all fit legibly.

Elements. box is [x, y, width, height] in px; elements sharing a group_id are one
designer-intended unit (for example a button shape and its label). hint_role and
hint_priority are a code heuristic you may override:
${facts}

Spatial relationships code detected (a <relation> b; correct or extend them):
${relationships}

Analyse, in this order:
1. Colours: the palette (3-6 colours as hex with a plain name and where/how much it is used),
   the scheme (one of ${schemes}) and one sentence on how colour builds contrast and focus.
2. Composition pattern of the source: one of ${patterns} (or a short name of your own),
   with one sentence on how it directs the eye. Then the pattern that best fits the
   TARGET canvas while keeping the same feeling, with one sentence on how to adapt.
3. Description: 3-4 sentences on how the design works: where visual weight sits, the reading
   path, how colour and space create movement and mood.
4. Every element: its role (one of ${roles}) and priority (1 = most important to the
   message, larger = less important; unique), plus a short "why" (under 15 words) for
   that priority. Titles/headlines, QR codes and dates carry what a viewer must read or act
   on: rank them above decoration, supporting copy and badges. qr_code and date are roles.
5. Relationships that must survive the re-layout (e.g. subhead below headline, label inside
   its badge, logo aligned with headline). Use relations ${relations}.
6. Difficulty: "easy" if moving and uniformly resizing elements will clearly fit the target
   canvas well, "hard" if the shape change or space pressure needs real rearrangement.

Reply with JSON only, in exactly this structure:
{"colors": {"palette": [{"hex": "#1F3B2D", "name": "forest green", "usage": "background, ~60%"}],
            "scheme": "analogous", "notes": "..."},
 "pattern": {"source": "asymmetrical_focal", "source_notes": "...",
             "target": "split_screen", "target_notes": "..."},
 "description": "...",
 "elements": [{"id": "...", "role": "...", "priority": 1, "why": "..."}],
 "relationships": [{"a": "subhead", "relation": "below", "b": "headline", "note": "one copy block"}],
 "difficulty": "easy" | "hard"}
