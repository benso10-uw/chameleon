"""Run tasks with tracing on and build an HTML report of every step.

    python -m engine.trace_run                     # every task in input/
    python -m engine.trace_run input/foo.json      # just these
    python -m engine.trace_run --no-model          # preset logic only

Writes to output/traces/:
    <task>.json          the raw trace (see engine/trace.py)
    <task>/*.png         renders of the source, preset layout, each attempt
    index.html           the report: open it in a browser
"""

import argparse
import html
import json
import sys
from pathlib import Path
from typing import Any

from engine.model import NoModel, default_model
from engine.resize import resize
from engine.trace import Trace

INPUT_DIR = Path("input")
TRACE_DIR = Path("output") / "traces"
ASSETS_DIR = Path("brand_kit/assets")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("tasks", nargs="*", type=Path, help="task JSON files (default: input/*.json)")
    parser.add_argument("--no-model", action="store_true", help="skip the LLM; preset logic only")
    args = parser.parse_args(argv)

    tasks = args.tasks or sorted(INPUT_DIR.glob("*.json"))
    if not tasks:
        print(f"No task files found in {INPUT_DIR}", file=sys.stderr)
        return 1
    model = NoModel() if args.no_model else default_model()
    print(f"Model: {_model_label(model)}")

    reports = []
    for path in tasks:
        task = json.loads(path.read_text(encoding="utf-8"))
        trace = Trace(path.stem)
        resize(task["source"], task["target_canvas"], model=model, trace=trace)
        trace.save(TRACE_DIR / f"{path.stem}.json")
        images = _render_candidates(path.stem, task, trace)
        reports.append((path.stem, task, trace.to_dict(), images))
        result = next((s for s in reversed(trace.steps) if s["kind"] == "result"), {})
        calls = [s for s in trace.steps if s["kind"] == "llm_call"]
        print(f"[{path.name}] winner: {result.get('winner')} ({result.get('reason')}); "
              f"{len(calls)} LLM call(s)")

    index = TRACE_DIR / "index.html"
    index.write_text(_html(reports, _model_label(model)), encoding="utf-8")
    print(f"Report: {index.resolve()}")
    return 0


def _model_label(model) -> str:
    if isinstance(model, NoModel):
        return "none (preset logic only)"
    name = getattr(model, "model", type(model).__name__)
    where = getattr(model, "base_url", None) or getattr(model, "host", "")
    return f"{name} @ {where}" if where else str(name)


# ---------------------------------------------------------------------------
# Rendering every candidate
# ---------------------------------------------------------------------------

def _render_candidates(stem: str, task: dict[str, Any], trace: Trace) -> dict[str, str]:
    """Render the source and each layout in the trace; returns step-key -> path
    relative to the report."""
    out_dir = TRACE_DIR / stem
    out_dir.mkdir(parents=True, exist_ok=True)
    images: dict[str, str] = {}
    jobs = [("source", task["source"])]
    for i, step in enumerate(trace.steps):
        if step["kind"] in ("preset", "attempt", "result") and step.get("layout"):
            jobs.append((f"step{i}", step["layout"]))
    for key, design in jobs:
        png = out_dir / f"{key}.png"
        try:
            from renderer.converter.convert import render_design_to_png
            render_design_to_png(design, ASSETS_DIR, png)
            images[key] = f"{stem}/{png.name}"
        except Exception as exc:  # a render failure shouldn't hide the trace
            images[key] = ""
            print(f"    render failed for {stem}/{key}: {exc}", file=sys.stderr)
    return images


# ---------------------------------------------------------------------------
# HTML report
# ---------------------------------------------------------------------------

def _e(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _pre(title: str, text: Any, open_: bool = False) -> str:
    if text in (None, ""):
        return ""
    if not isinstance(text, str):
        text = json.dumps(text, indent=2, ensure_ascii=False)
    return (f'<details{" open" if open_ else ""}><summary>{_e(title)} '
            f'<span class="muted">({len(text):,} chars)</span></summary><pre>{_e(text)}</pre></details>')


def _violations(rows: list[dict[str, Any]], kind: str) -> str:
    if not rows:
        return f'<p class="ok">No {kind}.</p>'
    body = "".join(
        f"<tr><td><code>{_e(v['kind'])}</code></td><td><code>{_e(v['element_id'])}</code></td>"
        f"<td>{_e(v['severity'])}</td><td>{_e(v['priority'])}</td><td>{_e(v['displacement'])}</td>"
        f"<td>{_e(v['message'])}</td></tr>" for v in rows)
    return ("<table><thead><tr><th>kind</th><th>element</th><th>severity</th><th>priority</th>"
            f"<th>displacement</th><th>message</th></tr></thead><tbody>{body}</tbody></table>")


def _report_block(report: dict[str, Any]) -> str:
    status = '<span class="badge pass">passes every rule</span>' if report["passes"] \
        else '<span class="badge fail">breaks rules</span>'
    return (f'<p>{status} rule cost <b>{report["rule_cost"]}</b> · signal cost '
            f'<b>{report["signal_cost"]}</b></p>'
            f'<h4>Rules ({len(report["rules"])})</h4>{_violations(report["rules"], "rule violations")}'
            f'<h4>Signals ({len(report["signals"])})</h4>{_violations(report["signals"], "signals")}')


def _thumb(images: dict[str, str], key: str, label: str) -> str:
    src = images.get(key)
    img = f'<a href="{_e(src)}" target="_blank"><img src="{_e(src)}" alt="{_e(label)}"></a>' if src \
        else '<div class="noimg">render failed</div>'
    return f'<figure>{img}<figcaption>{_e(label)}</figcaption></figure>'


def _step_html(i: int, step: dict[str, Any], images: dict[str, str]) -> str:
    kind = step["kind"]
    at = f'<span class="muted">t+{step["at_seconds"]}s</span>'
    if kind == "understand":
        roles = "".join(
            f'<tr{" class=changed" if step["heuristic_roles"].get(eid) != role or step["heuristic_priorities"].get(eid) != step["priorities"].get(eid) else ""}>'
            f'<td><code>{_e(eid)}</code></td><td>{_e(step["heuristic_roles"].get(eid))}</td>'
            f'<td>{_e(role)}</td><td>{_e(step["heuristic_priorities"].get(eid))}</td>'
            f'<td>{_e(step["priorities"].get(eid))}</td></tr>'
            for eid, role in sorted(step["roles"].items(), key=lambda kv: step["priorities"].get(kv[0], 99)))
        override = ' <span class="badge warn">space-pressure override → hard</span>' \
            if step["pressure_override"] else ""
        return (f'<div class="step"><h3>Understand {at}</h3>'
                f'<p>Decided by <b>{_e(step["decided_by"])}</b>; judged <b>{_e(step["judged_difficulty"])}</b>, '
                f'final difficulty <b>{_e(step["difficulty"])}</b>{override}</p>'
                f'<p class="muted">{_e(json.dumps(step["measurements"]))}</p>'
                '<table><thead><tr><th>element</th><th>heuristic role</th><th>final role</th>'
                f'<th>heuristic priority</th><th>final priority</th></tr></thead><tbody>{roles}</tbody></table>'
                '<p class="muted">Highlighted rows: the LLM changed the heuristic guess.</p></div>')
    if kind == "llm_call":
        usage = step.get("usage") or {}
        tokens = ", ".join(f"{k} {v}" for k, v in usage.items() if isinstance(v, (int, float)))
        if isinstance(usage.get("completion_tokens_details"), dict):
            reasoning_tokens = usage["completion_tokens_details"].get("reasoning_tokens")
            if reasoning_tokens is not None:
                tokens += f", reasoning_tokens {reasoning_tokens}"
        badge = {"usable": "pass", "llm_failure": "fail", "unavailable": "warn"}.get(step["outcome"], "warn")
        return (f'<div class="step llm"><h3>LLM call · {_e(step["purpose"])} '
                f'<span class="badge {badge}">{_e(step["outcome"])}</span> {at}</h3>'
                f'<p class="muted">{_e(step.get("model") or "")} · {_e(step["seconds"])}s'
                f'{" · " + _e(tokens) if tokens else ""}'
                f'{" · finish " + _e(step["finish_reason"]) if step.get("finish_reason") else ""}'
                f' · failures so far {_e(step["failures"])}</p>'
                f'{"<p class=err>" + _e(step["error"]) + "</p>" if step.get("error") else ""}'
                f'{_pre("Prompt", step["prompt"])}{_pre("Reasoning", step.get("reasoning"))}'
                f'{_pre("Reply", step.get("reply"), open_=step["outcome"] != "usable")}</div>')
    if kind in ("preset", "attempt"):
        title = "Preset layout" if kind == "preset" else f'Design {step["name"]}'
        if kind == "preset":
            note = f'<p>Decision: <b>{_e(step["decision"])}</b></p>'
        else:
            note = (f'<p>Improved on previous: <b>{_e(step["improved_on_previous"])}</b> · '
                    f'new best: <b>{_e(step["new_best"])}</b> · best so far: <b>{_e(step["best_so_far"])}</b></p>')
        return (f'<div class="step layout"><h3>{_e(title)} {at}</h3><div class="split">'
                f'{_thumb(images, f"step{i}", title)}<div>{note}{_report_block(step["report"])}</div></div>'
                f'{_pre("Layout JSON", step["layout"])}</div>')
    if kind == "tie_break":
        return (f'<div class="step"><h3>Tie-break {at}</h3><p>{_e(step["a"])} vs {_e(step["b"])} '
                f'(both rule cost {_e(step["rule_cost"])}), signal costs {_e(step["signal_cost"])} → '
                f'<b>{_e(step["winner"])}</b>, decided by {_e(step["decided_by"])}</p></div>')
    if kind == "loop_end":
        return (f'<div class="step"><h3>Check loop ended {at}</h3><p>{_e(step["reason"])} '
                f'({_e(step["attempts"])} design attempt(s))</p></div>')
    if kind == "result":
        report = _report_block(step["report"]) if step.get("report") else ""
        return (f'<div class="step result"><h3>Result: {_e(step["winner"])} {at}</h3><div class="split">'
                f'{_thumb(images, f"step{i}", "returned layout")}<div><p>{_e(step["reason"])}</p>{report}</div>'
                '</div></div>')
    if kind == "error":
        return (f'<div class="step"><h3>Error {at}</h3><p class="err">{_e(step["error"])}</p>'
                f'{_pre("Traceback", step["traceback"], open_=True)}</div>')
    return f'<div class="step"><h3>{_e(kind)} {at}</h3>{_pre("Data", step, open_=True)}</div>'


def _summary(stem: str, task: dict[str, Any], trace: dict[str, Any]) -> dict[str, Any]:
    steps = trace["steps"]
    understand = next((s for s in steps if s["kind"] == "understand"), {})
    result = next((s for s in reversed(steps) if s["kind"] == "result"), {})
    calls = [s for s in steps if s["kind"] == "llm_call"]
    tokens = sum((s.get("usage") or {}).get("total_tokens") or 0 for s in calls)
    report = result.get("report") or {}
    src, tgt = task["source"]["canvas"], task["target_canvas"]
    return {
        "stem": stem,
        "resize": f'{src["width"]}×{src["height"]} → {tgt["width"]}×{tgt["height"]}',
        "difficulty": f'{understand.get("difficulty", "?")} ({understand.get("decided_by", "?")})',
        "winner": result.get("winner", "?"),
        "passes": report.get("passes"),
        "rule_cost": report.get("rule_cost", "?"),
        "signals": len(report.get("signals", [])),
        "calls": f'{sum(s["outcome"] == "usable" for s in calls)} usable / '
                 f'{sum(s["outcome"] == "llm_failure" for s in calls)} failed',
        "tokens": tokens,
        "seconds": round(sum(s.get("seconds") or 0 for s in calls), 1),
    }


def _html(reports, model_label: str) -> str:
    rows, sections = [], []
    for stem, task, trace, images in reports:
        s = _summary(stem, task, trace)
        status = '<span class="badge pass">pass</span>' if s["passes"] else \
            '<span class="badge fail">fail</span>' if s["passes"] is False else ""
        rows.append(f'<tr><td><a href="#{_e(stem)}">{_e(stem)}</a></td><td>{_e(s["resize"])}</td>'
                    f'<td>{_e(s["difficulty"])}</td><td>{_e(s["winner"])} {status}</td>'
                    f'<td>{_e(s["rule_cost"])}</td><td>{_e(s["signals"])}</td><td>{_e(s["calls"])}</td>'
                    f'<td>{_e(s["tokens"])}</td><td>{_e(s["seconds"])}</td></tr>')
        strip = [_thumb(images, "source", "source design")]
        for i, step in enumerate(trace["steps"]):
            if step["kind"] == "preset":
                strip.append(_thumb(images, f"step{i}", "preset layout"))
            elif step["kind"] == "attempt":
                strip.append(_thumb(images, f"step{i}", step["name"]))
        timeline = "".join(_step_html(i, step, images) for i, step in enumerate(trace["steps"]))
        sections.append(f'<section id="{_e(stem)}"><h2>{_e(stem)}</h2><p class="muted">{_e(s["resize"])} · '
                        f'difficulty {_e(s["difficulty"])} · winner <b>{_e(s["winner"])}</b></p>'
                        f'<div class="strip">{"".join(strip)}</div>{timeline}</section>')
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Chameleon traces</title>
<style>
:root {{ --bg:#fafaf9; --fg:#1c1917; --muted:#78716c; --card:#fff; --line:#e7e5e4; --pass:#15803d;
        --fail:#b91c1c; --warn:#b45309; --hl:#fef3c7; --code:#f5f5f4; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#1c1917; --fg:#f5f5f4; --muted:#a8a29e; --card:#292524;
        --line:#44403c; --pass:#4ade80; --fail:#f87171; --warn:#fbbf24; --hl:#422006; --code:#1c1917; }} }}
* {{ box-sizing:border-box; }}
body {{ margin:0; padding:24px 16px; background:var(--bg); color:var(--fg);
        font:14px/1.5 system-ui, -apple-system, Segoe UI, sans-serif; max-width:1400px; margin:auto; }}
h1 {{ margin:0 0 4px; }} h2 {{ margin-top:40px; border-bottom:1px solid var(--line); padding-bottom:6px; }}
h3 {{ margin:0 0 8px; font-size:15px; }} h4 {{ margin:12px 0 4px; font-size:13px; }}
.muted {{ color:var(--muted); font-weight:normal; }} .ok {{ color:var(--pass); margin:0; }}
.err {{ color:var(--fail); }}
table {{ border-collapse:collapse; width:100%; font-size:12.5px; margin:4px 0; }}
th, td {{ border-bottom:1px solid var(--line); padding:4px 8px; text-align:left; vertical-align:top; }}
tr.changed td {{ background:var(--hl); }}
code, pre {{ font-family:ui-monospace, SFMono-Regular, Consolas, monospace; }}
pre {{ background:var(--code); border:1px solid var(--line); padding:10px; overflow:auto; max-height:480px;
       white-space:pre-wrap; word-break:break-word; font-size:12px; }}
details {{ margin:6px 0; }} summary {{ cursor:pointer; font-weight:600; }}
.step {{ background:var(--card); border:1px solid var(--line); border-left:4px solid var(--line);
         border-radius:6px; padding:12px 14px; margin:10px 0; }}
.step.llm {{ border-left-color:#6366f1; }} .step.layout {{ border-left-color:#0ea5e9; }}
.step.result {{ border-left-color:var(--pass); }}
.badge {{ display:inline-block; padding:1px 8px; border-radius:999px; font-size:12px; font-weight:600;
          border:1px solid currentColor; }}
.badge.pass {{ color:var(--pass); }} .badge.fail {{ color:var(--fail); }} .badge.warn {{ color:var(--warn); }}
.strip {{ display:flex; gap:12px; overflow-x:auto; padding-bottom:6px; }}
.split {{ display:grid; grid-template-columns:minmax(0, 380px) 1fr; gap:16px; align-items:start; }}
@media (max-width: 800px) {{ .split {{ grid-template-columns:1fr; }} }}
figure {{ margin:0; flex:0 0 auto; }}
figure img {{ max-width:380px; max-height:300px; width:auto; height:auto; display:block;
              border:1px solid var(--line); border-radius:4px; }}
.strip figure img {{ max-width:320px; max-height:220px; }}
figcaption {{ font-size:12px; color:var(--muted); margin-top:4px; }}
.noimg {{ width:200px; height:120px; display:grid; place-items:center; border:1px dashed var(--line);
          color:var(--muted); }}
</style></head><body>
<h1>Chameleon traces</h1>
<p class="muted">Model: {_e(model_label)}</p>
<table><thead><tr><th>task</th><th>resize</th><th>difficulty</th><th>winner</th><th>rule cost</th>
<th>signals</th><th>LLM calls</th><th>tokens</th><th>LLM seconds</th></tr></thead><tbody>{"".join(rows)}</tbody></table>
{"".join(sections)}
</body></html>
"""


if __name__ == "__main__":
    raise SystemExit(main())
