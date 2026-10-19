# Chameleon

Chameleon takes a finished design (a banner, a poster, an ad) and **retargets it
to a new canvas size** — e.g. a 1080×1080 square into a 728×90 leaderboard —
by *rearranging* its elements instead of stretching them.

```
input:  task JSON  = source design (canvas + elements) + target canvas size
           |
           v
        resize()   (engine/resize/resize_logic.py)
           |
           v
output: <task>_output.json   resized design, same element ids
        <task>_output.png    rendered preview
```

Both the JSON and the PNG are evaluated — see `RULES.md`. Vocabulary used in the
code (role, priority, rule, signal, preset layout, …) is defined in `CONTEXT.md`.

---

## How it works

Every design goes through four steps. Steps 1 and 3 *may* call an AI model;
everything else is plain Python.

### 1. Understand the design — `engine/analyze/`

Designs have no labels, so the engine first works out what each element is.

- **Code** guesses a **role** for each element (headline, logo, product, CTA,
  background, decoration, legal…) and a **priority** (how much it matters).
- **Code** measures the problem: how much the aspect ratio changes and the
  **space pressure** (how much room the elements need vs. how much the target
  canvas has). Text is measured with the real fonts (`text_metrics.py`).
- **Code** recognises **dates** and **QR codes** and ranks them, with the
  headline, above everything else: they carry what a viewer must read or act on.
- **ML (optional): one design-analysis call.** The model is shown the rendered
  source design (`visual.py`) and the element facts, and returns a structured
  analysis (`analysis.py`): the colour palette and scheme (monochromatic,
  analogous, complementary…), the composition pattern of the source and the
  pattern that suits the target (centred, asymmetrical focal, Z-pattern,
  split-screen…), a 3–4 sentence description of how the design works, every
  element's role and priority with a short reason, the relationships that must
  survive (subhead below headline…), and **easy** or **hard**. Code adds the
  exact technical facts (position, size, layer, font size, colour).
  If the model refuses images it gets the colours code extracts instead, and
  failing that the element facts alone. If no model is available, the code's
  guesses are used as-is.

### 2. Make the preset layout — `engine/resize/preset.py`, `repair.py`

A deterministic layout, no ML. It groups elements into blocks and arranges them
by the target's shape: a centred column (tall/square), hero image + column (wide),
or one row of cells (very wide strips). Fonts are shrunk only as far as needed,
the background is stretched to cover, and `repair.py` nudges small mistakes
(a few pixels off-canvas, text slightly too big) back into place.

### 3. Decide: done, or ask the model — `resize_logic.py`, `hard_path.py`

- **Easy design and the preset passes every rule →** return the preset. No model call.
- **Otherwise →** the **check loop** starts:
  1. **ML:** the model gets the element list and the design analysis, and is
     asked to keep the design's feeling while adapting it to the target pattern:
     highest priorities placed first, relationships kept, the canvas used. It
     replies with a full new layout as JSON, a 1–2 sentence **why** for every
     element and group, and a 2–3 sentence **summary** of the new design.
     `rationale.py` extracts that reasoning. Every later attempt reads back the
     earlier attempts' summaries and scores, plus the previous attempt's
     per-element reasoning and problems, and is asked to improve on them.
  2. Code restores anything the model isn't allowed to change (text, colours,
     fonts, images), repairs small errors, and checks the result.
  3. Repeat up to 2–3 attempts, stopping early when an attempt doesn't improve.
  4. The **best candidate wins** — fewest rule breaks first, then quality signals.
     If the model can't beat the preset, the preset is returned.
     **ML (optional):** when two candidates tie, the model can be asked which
     looks better.

Unusable replies (bad JSON, missing ids) are retried with the error quoted back,
up to 3 in a row / 6 total per design, then the engine gives up and keeps the
best layout it has. If anything crashes, a simple scaled-to-fit layout is returned
so one bad design never stops the run.

### 4. Check — `engine/check/checker.py`

Used to score every candidate. It reports:

- **Rules** (must pass): every element present exactly once and visible,
  required fields intact, images not distorted, groups kept together, text fits
  its box at ≥ 6px.
- **Signals** (quality hints, never fail a layout alone): overlaps, small text,
  elements off-canvas, poor alignment, wasted space.

### How the model is called — `engine/model/`

All model calls go through one tiny interface: `complete(prompt, images=None) -> str`.
`llm.py` wraps it as `ask_json()`, which sends the prompt, pulls JSON out of the
reply, validates it, and retries on bad replies. Available back-ends:

| Back-end | When it is used |
| --- | --- |
| `NoModel` | `--no-model`, or nothing configured. Never calls anything — preset only. |
| `OpenAICompatibleModel` | `CHAMELEON_LLM_BASE_URL` + `CHAMELEON_LLM_MODEL` set in `.env` (e.g. hosted Qwen). |
| `OllamaModel` | `CHAMELEON_OLLAMA_MODEL` set (local model through Ollama). |
| `GeminiModel` | Picked in the web workbench, using `GEMINI_API_KEY`. |

Layout calls send **text/JSON** only. The design-analysis call also sends the
rendered source design as a PNG (at most 1024px) when the adapter supports
images; a model that rejects it (HTTP 400/404/413/415/422) is asked again
without it.

### Other pieces

| Path | What it is |
| --- | --- |
| `engine/main.py` | Command-line runner: loads tasks, calls `resize()`, writes JSON + PNG. |
| `engine/workbench.py` + `web/` | Local website for testing one design and watching each step. |
| `engine/tracing/` | Records every step (prompts, replies, reports) and builds an HTML report. |
| `engine/benchmark.py` | Compares Gemini reasoning settings on one task. |
| `renderer/` | Vendored renderer that turns design JSON into a PNG with the real fonts/images. |
| `brand_kit/` | Fonts and image assets referenced by the designs. |
| `input/` | Task JSON files (three samples: Level 1 warm-up, Level 2 core, Level 3 advanced). |
| `output/` | Where results land. |
| `tests/` | pytest suite. |

---

## How to run it

### First-time setup (Windows PowerShell, from the repo folder)

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt
```

Optional — to use a model, copy `.env.example` to `.env` and fill in the
`CHAMELEON_LLM_*` settings (or `CHAMELEON_OLLAMA_MODEL`, or `GEMINI_API_KEY`).
Without a `.env`, everything still runs using the preset logic only.

> On macOS/Linux, use `.venv/bin/python` instead of `.\.venv\Scripts\python.exe`.

### Option A — the website (workbench)

Best for testing one design, comparing attempts side by side and seeing the model's prompts and replies.

```powershell
.\.venv\Scripts\python.exe -m engine.workbench --open-browser
```

…or just double-click **`start-workbench.cmd`**. It opens http://127.0.0.1:8765.
Keep the window open and press Ctrl+C to stop.

1. Pick a sample (or import a task JSON) and set the target width/height.
2. Choose **Preset only** (no API calls) or a model provider.
3. Click **Run selected design**, then click through **Preset / attempt 1 /
   attempt 2 / Returned layout** to compare.

Runs are saved under `output/workbench/<run-id>/`. Full guide: [docs/WORKBENCH.md](docs/WORKBENCH.md).

### Option B — test a single file from the command line

```powershell
# Preset logic only, no model calls (fast, offline)
.\.venv\Scripts\python.exe -m engine.main input/chameleon_sample_level1_warmup.json --no-model

# Same file, using the model configured in .env
.\.venv\Scripts\python.exe -m engine.main input/chameleon_sample_level1_warmup.json

# Write the results somewhere else
.\.venv\Scripts\python.exe -m engine.main input/chameleon_sample_level3_advanced.json --no-model --output-dir output/my-test
```

Results: `output/<task>_output.json` and `output/<task>_output.png`.

### Option C — run every task in `input/`

```powershell
.\.venv\Scripts\python.exe -m engine.main            # with the model from .env
.\.venv\Scripts\python.exe -m engine.main --no-model # preset only
```

### Option D — one task with a full step-by-step trace

```powershell
.\.venv\Scripts\python.exe -m engine.tracing.trace_run input/chameleon_sample_level3_advanced.json --no-model
```

Writes `output/traces/<task>.json`, a PNG per candidate, and an HTML report at
`output/traces/index.html`. Drop `--no-model` to trace a model run.

### Compare model reasoning settings (Gemini)

```powershell
.\.venv\Scripts\python.exe -m engine.benchmark input/chameleon_sample_level3_advanced.json --model gemini-3.5-flash-lite --reasoning minimal low --attempts 2
```

Results appear in the workbench history.

### Run the tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

### Docker (how the submission is scored)

```bash
docker build --platform linux/amd64 -t my-chameleon-engine .
docker run --rm --network none \
  -v $(pwd)/input:/app/input \
  -v $(pwd)/output:/app/output \
  my-chameleon-engine
```

`--network none` matches scoring: the container cannot reach a hosted model, so
anything the engine needs (including a local model's weights) must be installed
at **build** time via `requirements.txt` / the `Dockerfile`. With no model
reachable, the engine falls back to the preset logic.

---

## Notes

- **The self-check is not the official scorer.** `engine/main.py` prints quick
  structural warnings (missing ids, off-canvas elements). A warning doesn't stop
  the run — e.g. Level 2's corner-bleeding decorative circle is intentional.
  See `RULES.md` for what is actually graded.
- **The Level 3 sample references a missing asset** (`solstice_holiday_badge_2025.png`)
  on purpose; a placeholder is rendered in its place.
- **Placeholder PNGs** (plain labelled boxes instead of real text/images) mean the
  renderer failed to import — check that `cr-renderer` installed from
  `requirements.txt`. Don't judge layout quality from those.
- Custom images must go in `brand_kit/assets/` and be referenced by `asset_id`.
  A plain PNG/JPEG cannot be used as input — the engine needs the editable elements.

## See also

- [docs/WORKBENCH.md](docs/WORKBENCH.md) — workbench guide
- [docs/QUALITY-AND-SPEED.md](docs/QUALITY-AND-SPEED.md) — measured results
- [docs/adr/0001-llm-generates-hard-layouts.md](docs/adr/0001-llm-generates-hard-layouts.md) — why easy designs skip the model
- `CONTEXT.md` — glossary
- `chameleon_design_schema_v0.9.json` — full input/output field spec
- `RULES.md`, `FAQ.md` — challenge rules
