# Testing one design

Double-click `start-workbench.cmd` in the repository folder. Keep its window open.
It starts the local server and opens http://127.0.0.1:8765. If the server is
already running, simply open that address. Stop the server with Ctrl+C.

First-time setup, from PowerShell in the repository folder:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt
.\.venv\Scripts\python.exe -m engine.workbench --open-browser
```

## Workflow

1. Choose one sample, or import a task JSON. A PNG/JPEG alone does not contain
   the editable elements this engine needs. Custom image assets belong in
   `brand_kit/assets`; the task references them through `asset_id`.
2. Change the target width and height if desired.
3. Choose **Preset only** for a quick offline preview, or choose a model provider.
4. For Gemini, start with `gemini-3.5-flash-lite` and **Minimal**. Fetch models
   to see IDs advertised to your key; an advertised model may still reject
   generation for your account. The supplied key is stored in ignored `.env`
   as `GEMINI_API_KEY`; the browser only receives a saved/not-saved indicator.
5. Click **Run selected design**. The trace updates at stage boundaries, with
   a pending indicator while a model request is in flight. This version does
   not stream individual generated tokens.
6. Select **Preset layout**, **attempt 1**, **attempt 2**, or **Returned layout**
   to compare previews. Click an image to open it at full resolution.
7. Expand rule violations, quality observations, or provider reasoning summaries.
   Enable **Show prompts and raw replies** for the full diagnostic detail.
8. Open previous runs from history. New runs are saved separately under
   `output/workbench/<run-id>/`, including trace.json, output.json and PNGs.
   The older `output/traces/index.html` and its files remain unchanged; their
   traces are also available in the workbench history.

## Settings

- **Design attempts:** 1 is quickest; 2 is the default; 3 allows more refinement.
- **Reply token cap:** 4096 by default. Reasoning can consume this budget, so
  lowering it too far may truncate JSON and cause retries.
- **Timeout:** 90 seconds per request by default. Stop waits for the in-flight
  request to finish; completed steps are retained.
- **Refine easy designs:** enabled for model runs so Levels 1 and 2 can receive
  design attempts even when their presets pass.
- **Use the model to assign roles:** off by default to save a request. Enable
  when the inferred roles look wrong.
- **Use the model to judge ties:** off by default. Rule costs decide first,
  then measured quality signals. Enable to let the model judge equal rule costs.
- **Reasoning:** Gemini 2.5 Flash supports off where available. Newer Gemini
  models use thinking levels; Minimal is not a guarantee of zero reasoning.
  Qwen compatible hosts receive `chat_template_kwargs.enable_thinking`; other
  compatible hosts receive `reasoning_effort`. Host support varies; use provider
  default if the host rejects the setting. Ollama uses its model defaults.

For hosted Qwen, the workbench now starts with **Off**. If a request finishes
with `length`, Qwen gets one recovery with thinking disabled and at least
8192 tokens. A second truncated response stops with an explanatory message;
the same capped request is never repeated three times. Other providers stop
on truncation so you can adjust their supported reasoning setting or token cap.

The workbench listens on localhost only. Credentials entered in the form are
held for that run, are not written to run records, and are never returned to
the browser by the configuration endpoint. Local design files and saved traces
remain on your machine. A model run sends the design's text and element facts
to the provider you select.

## Command-line alternatives

Run just one sample without a model:

```powershell
.\.venv\Scripts\python.exe -m engine.main input/chameleon_sample_level3_advanced.json --no-model --output-dir output/my-test
```

Run one traced task:

```powershell
.\.venv\Scripts\python.exe -m engine.tracing.trace_run input/chameleon_sample_level3_advanced.json --no-model
```

Compare Gemini reasoning settings; results appear in workbench history:

```powershell
.\.venv\Scripts\python.exe -m engine.benchmark input/chameleon_sample_level3_advanced.json --model gemini-3.5-flash-lite --reasoning minimal low --attempts 2
```

## Verification

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

See `QUALITY-AND-SPEED.md` for measured results and remaining quality limits.
