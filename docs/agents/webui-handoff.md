# Chameleon web UI: handoff for the next LLM

This guide describes the implementation and observed behavior as of 16 October
2026. Check the current code and saved run before assuming a provider/model
still behaves the same way. This is a local Python application, not a deployed
website. No Node build, Docker, or frontend package installation is needed.

## Start or resume

1. Work from this repository's root. Inspect `git status --short` first and
   preserve unrelated changes. Read `CONTEXT.md` and relevant `docs/adr/`.
2. Try http://127.0.0.1:8765 first; do not start duplicate servers. A response
   from `/api/config` confirms the server is listening and reports renderer
   availability. This endpoint returns only credential-presence indicators.
3. If it is not running, double-click `start-workbench.cmd`, or use PowerShell:

   ```powershell
   .\.venv\Scripts\python.exe -m engine.workbench --open-browser
   ```

4. If `.venv` is missing, follow `docs/WORKBENCH.md` for first-time setup.
5. Keep the server process alive while the user tests. Ctrl+C stops it.
   After Python changes, restart the process; after HTML/CSS/JS changes,
   refresh the browser. Editing files does not reload imported Python modules.
6. The server binds to `127.0.0.1`, checks local Host/Origin, and permits one
   active run at a time. Never expose it publicly as a workaround for browser
   access problems. If browser tooling is unavailable, report which UI checks
   are unverified and use local API/tests for independent checks.

## Operate the screen

The left sidebar controls the NEXT run. The center displays the loaded task
and selected candidate. The trace below describes the displayed run. Choosing
new settings does not rerun a saved result.

1. **Sample or imported task:** choose `Level 1 · warmup`, `Level 2 · core`,
   or `Level 3 · advanced`. Sample selection resets target dimensions to that
   task's defaults. Core is 1600×400; Advanced is 728×90.
2. **Import task JSON:** accepts an object with `source.canvas`,
   `source.elements`, and `target_canvas`. A flat PNG/JPEG cannot provide the
   editable elements required by this engine. Assets must be inside
   `brand_kit/assets`, referenced by `asset_id`; fonts are in `brand_kit/fonts`.
3. **Target width / Height:** set dimensions before running.
4. **Provider:** the page initially selects **Preset only · no API calls**.
   This intentionally produces ZERO model calls on EVERY level, even Advanced.
   Do not diagnose zero calls as a provider failure until checking this selector.
5. For hosted Qwen, select **OpenAI-compatible API**. The configured model and
   URL populate from local environment settings. Verify the model ID and URL;
   choose **Reasoning → Off**. The browser form may leave the API-key field
   blank when a key is saved locally.
6. For Gemini, select **Google AI Studio · Gemini**. The tested initial choice
   is `gemini-3.5-flash-lite` / **Minimal**. **Fetch Gemini models** fills the
   model-name suggestions. An advertised ID may still reject generation for
   that account. Do not silently substitute a different provider or model.
7. Expand **Run settings**. For a model to generate a layout on easy/Core
   designs, keep **Ask the model to refine easy designs** checked. Otherwise
   a passing easy preset returns early. Two attempts is the UI default.
   Model-assigned roles and model tie judging default off to save requests.
8. Click **Run selected design**. Observe the pending status and live stages.
   This is stage-level polling, NOT token-by-token streaming. A long request
   can show no new step until its response or timeout.
9. Click **Preset layout**, **attempt 1**, **attempt 2**, or **Returned layout**
   to inspect each candidate. Click a preview image for full resolution.
10. Expand rule violations and quality observations. Provider reasoning is
    collapsed by default; enable **Show prompts and raw replies** to inspect
    the exact prompt and response.
11. **Previous runs** opens saved results. It does not restore all provider
    settings to the sidebar; verify provider/settings before clicking Run again.
12. **Stop after current request** waits for the in-flight call to return, then
    cancels subsequent work. It does not immediately abort the network request.

## Interpret the trace correctly

| Observation | Meaning / next action |
| --- | --- |
| 0 model calls | Check Provider. Preset-only intentionally makes none. For Core with a model selected, also check refinement and role-assignment settings. |
| `Code-assigned roles; saved one model call` | Understanding used heuristics; layout generation may still call the model. |
| `llm_start` / Model working | Request started; wait for `llm_call` or timeout. |
| Model replied / `usable` | Reply parsed and was accepted as a design attempt; it can still break design rules. |
| `finish: length` / `MAX_TOKENS` | Token cap truncated the response. Inspect usage, reasoning tokens and reply. |
| `model_recovery` | Qwen gets one retry with thinking off and at least 8192 tokens. Repeated truncation stops; other providers stop so settings can be adjusted. |
| Rule violations | Candidate breaks geometry/content rules; see exact affected elements. |
| Rules pass | Structural validity only. Inspect PNG for visual quality and small text. |
| Returned preset after model calls | Model ran, but none of its candidates beat the preset. This is not proof that the API call failed. |
| HTTP 429 / gateway failure | Quota/provider availability problem. The call budget stops to avoid repeated expensive failures. |
| Missing holiday asset on Advanced | Intentional sample adversary: `solstice_holiday_badge_2025.png` is absent. UI shows a warning and renderer shows a placeholder. |

Known wording issue: a preset-only trace may say `hard: the LLM lays it out
from scratch` and later `LLM-failure budget spent or model unavailable`.
Those messages describe routing, but are misleading when NoModel was selected.
Trust the selected provider, actual `llm_call` records and run metadata; do not
claim a model ran based on that routing sentence.

## Diagnose the actual failed run first

Runs are stored at `output/workbench/<run-id>/`:

- `run.json`: task, model name, reasoning setting, state, trace steps, preview
  paths, warnings and elapsed time. Credentials are excluded.
- `trace.json`: step timeline, including prompts, responses, reported usage,
  provider reasoning/summary and validation reports.
- `output.json`: the returned design, created for completed runs.
- `step<N>.png`: layouts rendered at the matching zero-based step index.
- Source previews are cached under `output/workbench/sources/`.

Sort `*/run.json` by modification time and inspect the latest relevant task.
Old `output/traces/*.json` runs are also displayed in history; the original
static `index.html` is preserved. Imported tasks are held in memory; opening
a saved run reimports its task. Arbitrary imports are not a permanent catalogue.

The Core failure that motivated the latest fix used Qwen / provider default
reasoning with a 4096-token cap. Three responses consumed all 4096 tokens on
reasoning and returned no JSON. The verified Qwen Off / 8192-token run returned
usable JSON in 7.61s with 552 answer tokens and zero reasoning tokens. Its layout
still broke rules, so the preset remained the returned result. These are single
observations, not guarantees of future provider latency or design quality.

## Code map and API

| Path | Responsibility |
| --- | --- |
| `engine/workbench.py` | Local HTTP server, task validation, run worker, saved history, credential handling, preview rendering |
| `web/index.html`, `web/style.css`, `web/app.js` | Controls, comparison screen, polling, trace presentation |
| `engine/resize/resize_logic.py` | Understanding/preset/model routing, refinement and run options |
| `engine/resize/hard_path.py` | Layout prompt, group transforms, candidate check loop and ranking |
| `engine/resize/preset.py` | Deterministic layout and compact banner arrangement |
| `engine/model/model.py` | Compatible API/Ollama adapters, Qwen thinking switch and truncation recovery |
| `engine/model/gemini.py` | Native Gemini adapter, thinking levels/budgets, model discovery |
| `engine/model/llm.py` | JSON parsing/retries, token-limit detection, request start/result traces |
| `engine/tracing/trace.py` | Trace storage and stage callback |
| `tests/test_workbench.py` | Group, image, credential, path, cancellation and provider-failure regressions |

The UI uses GET `/api/config`, `/api/tasks/<id>`, `/api/history`,
`/api/runs/<id>?after=<step-count>` and POST `/api/import`, `/api/models`,
`/api/runs`, `/api/cancel/<id>`. Inspect the handlers for current payloads.
Polling returns only steps after the requested count plus current run metadata.

## Verify changes and leave a reliable handoff

```powershell
.\.venv\Scripts\python.exe -m pytest -q
node --check web/app.js
git diff --check
```

Then test one task with the provider/settings affected by the change. Check
the actual saved trace for usable replies, returned candidate, warnings and
PNG appearance. A passing unit test or HTTP 200 is not enough to claim good
design quality. The last engine test run had 62 passing tests.

Desktop browser functionality was verified previously. Mobile, full keyboard,
contrast and reduced-motion checks were not completed because browser access
was disabled during that pass. Recheck these when access is available; do not
copy a previous verification claim as evidence for new changes.

Preserve existing results, secrets and unrelated edits. Do not print `.env`
or place credentials in docs, screenshots, prompts, commits or run records.
The workbench reads `GEMINI_API_KEY` and the `CHAMELEON_LLM_*` configuration.
The form's supplied key is used for the run and is not persisted by the form.
Model calls transmit task text/element facts to the selected provider; this
implementation does not send rendered images for visual critique.
