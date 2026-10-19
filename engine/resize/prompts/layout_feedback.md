This is design attempt ${attempt}. Your earlier attempts, oldest first, with the pattern you
chose, your own summary and how each scored (rule_cost must reach 0; lower signal_cost is better):
${history}

Your previous attempt, with your justification ("why") for each placement:
${geometry}
Your summary of it: ${summary}
${missing}
Its violation report (rules must be fixed first, highest-priority elements first; displacement
is how far an element moved from its source position, as a fraction of the canvas):
${report}

Re-read your justifications and summary against this report before changing anything:
- Keep placements whose reasoning still holds and that the report does not flag.
- Rework every flagged element, highest priority first, and anything your own reasoning
  now shows was a weak choice.
- Then look for one improvement beyond the fixes: better use of empty space, a clearer reading
  path, a stronger focal point, or relationships kept more faithfully.
In each "why", say what changed from the previous attempt and why, or "Kept: <reason>".
The summary must say how this attempt improves on the previous one.
Produce the improved layout in the same JSON structure.
