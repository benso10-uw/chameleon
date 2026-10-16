import copy
import io
import json
import urllib.error
from pathlib import Path
from unittest.mock import patch

import pytest

from engine.check.checker import check
from engine.model.gemini import GeminiModel, thinking_config
from engine.model.llm import LLMBudget, UnusableReply, ask_json
from engine.model.model import NoModel, OpenAICompatibleModel
from engine.resize import resize
from engine.resize.hard_path import _apply
from engine.tracing.trace import Trace
from engine.workbench import Handler, Run, validate_task
from tests.helpers import load_task


def compact_reply(task):
    source = task["source"]
    return {"elements": [{k: e[k] for k in ("id", "x", "y", "width", "height", "font_size") if k in e}
                         for e in source["elements"] if not e.get("group_id")],
            "groups": [{"group_id": gid, "x": 10, "y": 10, "scale": .1}
                       for gid in dict.fromkeys(e["group_id"] for e in source["elements"] if e.get("group_id"))]}


def test_group_transforms_preserve_arrangement_and_enforce_six_pixel_floor():
    task = load_task("level3_advanced")
    out = _apply(task["source"], task["source"]["canvas"], compact_reply(task))
    report = check(task["source"], out, task["source"]["canvas"])
    assert not {v.kind for v in report.rules} & {"group_broken", "text_below_floor"}
    assert min(e["font_size"] for e in out["elements"] if e.get("group_id") and e["type"] == "text") >= 6


def test_image_placement_boxes_contain_source_aspect_ratio():
    task = load_task("level3_advanced")
    reply = compact_reply(task)
    for el in reply["elements"]:
        if el["id"] == "product":
            el.update(x=10, y=10, width=100, height=60)
    out = _apply(task["source"], task["source"]["canvas"], reply)
    product = next(e for e in out["elements"] if e["id"] == "product")
    assert (product["x"], product["y"], product["width"], product["height"]) == (30, 10, 60, 60)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1, 0, True])
def test_invalid_group_scales_are_rejected(bad):
    task = load_task("level3_advanced")
    reply = compact_reply(task)
    reply["groups"][0]["scale"] = bad
    with pytest.raises(UnusableReply):
        _apply(task["source"], task["target_canvas"], reply)


def test_missing_or_duplicate_groups_are_retried_instead_of_silently_dropped():
    task = load_task("level3_advanced")
    reply = compact_reply(task)
    reply["groups"].pop()
    with pytest.raises(UnusableReply, match="missing groups"):
        _apply(task["source"], task["target_canvas"], reply)


def test_dense_banner_uses_width_and_keeps_headline_readable():
    task = load_task("level3_advanced")
    before = copy.deepcopy(task)
    out = resize(task["source"], task["target_canvas"], model=NoModel())
    headline = next(e for e in out["elements"] if e["id"] == "headline")
    assert headline["font_size"] >= 10
    assert headline["width"] >= task["target_canvas"]["width"] * .8
    assert check(task["source"], out, task["target_canvas"]).passes
    assert task == before


def test_preset_only_records_no_model_requests():
    task = load_task("level3_advanced")
    trace = Trace()
    resize(task["source"], task["target_canvas"], model=NoModel(), trace=trace)
    assert not any(s["kind"] in ("llm_call", "llm_start") for s in trace.steps)


def test_gemini_reasoning_controls_do_not_claim_unsupported_off():
    assert thinking_config("gemini-2.5-flash", "off")["thinkingBudget"] == 0
    assert thinking_config("gemini-3.5-flash-lite", "minimal")["thinkingLevel"] == "MINIMAL"
    with pytest.raises(ValueError):
        thinking_config("gemini-3.8-flash", "off")
    with pytest.raises(ValueError):
        thinking_config("gemini-3.8-flash", "minimal")


def test_gemini_separates_thought_summaries_and_normalizes_usage():
    reply = {"candidates": [{"content": {"parts": [{"thought": True, "text": "Summary"},
                                                  {"text": '{"ok": true}'}]}, "finishReason": "STOP"}],
             "usageMetadata": {"promptTokenCount": 20, "candidatesTokenCount": 5, "thoughtsTokenCount": 8, "totalTokenCount": 33}}
    with patch("engine.model.gemini.google_json", return_value=reply) as mock:
        model = GeminiModel("gemini-3.5-flash-lite", "secret", "low")
        assert model.complete("prompt") == '{"ok": true}'
        assert model.last_call["reasoning"] == "Summary"
        assert model.last_call["usage"]["completion_tokens_details"]["reasoning_tokens"] == 8
        assert mock.call_args.args[2]["generationConfig"]["thinkingConfig"]["thinkingLevel"] == "LOW"


@pytest.mark.parametrize("code", [429, 502, 503, 504, 524])
def test_infrastructure_errors_do_not_repeat_expensive_calls(code):
    model = OpenAICompatibleModel("qwen", "https://example.invalid", "secret")
    error = urllib.error.HTTPError("https://example.invalid", code, "Unavailable", {}, io.BytesIO(b"secret"))
    with patch("urllib.request.urlopen", side_effect=error) as call:
        trace = Trace()
        from engine.tracing.trace import tracing
        with tracing(trace):
            assert ask_json(model, "prompt", lambda x: x, LLMBudget()) is None
        assert call.call_count == 1
        assert "secret" not in json.dumps(trace.to_dict())


@pytest.mark.parametrize("asset", ["../../.env", "C:/Windows/win.ini", "../fonts/file.ttf"])
def test_import_cannot_reference_files_outside_assets(asset):
    task = load_task("level1_warmup")
    task["source"]["elements"][0]["asset_id"] = asset
    with pytest.raises(ValueError, match="inside brand_kit"):
        validate_task(task)


def test_import_rejects_nonfinite_canvas_and_duplicate_ids():
    task = load_task("level1_warmup")
    task["target_canvas"]["width"] = float("nan")
    with pytest.raises(ValueError):
        validate_task(task)
    task = load_task("level1_warmup")
    task["source"]["elements"].append(task["source"]["elements"][0])
    with pytest.raises(ValueError):
        validate_task(task)


def test_local_server_blocks_foreign_origins_and_dns_rebinding():
    handler = object.__new__(Handler)
    handler.server = type("Server", (), {"server_port": 8765})()
    handler.headers = {"Host": "127.0.0.1:8765", "Origin": "https://foreign.example"}
    assert not handler.local_request()
    handler.headers = {"Host": "foreign.example:8765"}
    assert not handler.local_request()
    handler.headers = {"Host": "localhost:8765", "Origin": "http://localhost:8765"}
    assert handler.local_request()


def test_run_persists_artifacts_without_configuration_secrets(tmp_path, monkeypatch):
    import engine.workbench as wb
    monkeypatch.setattr(wb, "OUTPUT", tmp_path)
    monkeypatch.setattr(wb, "render", lambda design, path: None)
    task = load_task("level1_warmup")
    run = Run(task, "sample", NoModel(), {"api_key": "do-not-persist", "attempts": 1})
    run.work()
    saved = (run.folder / "run.json").read_text()
    assert "do-not-persist" not in saved
    assert json.loads(saved)["state"] == "done"
    assert (run.folder / "trace.json").is_file()
    assert (run.folder / "output.json").is_file()


def test_cancelled_run_stays_cancelled(tmp_path, monkeypatch):
    import engine.workbench as wb
    monkeypatch.setattr(wb, "OUTPUT", tmp_path)
    monkeypatch.setattr(wb, "render", lambda design, path: None)
    run = Run(load_task("level1_warmup"), "sample", NoModel(), {})
    run.cancel.set()
    run.work()
    assert run.data["state"] == "cancelled"
    assert not (run.folder / "output.json").exists()


def test_qwen_token_exhaustion_changes_settings_once_then_gets_json():
    model = OpenAICompatibleModel("qwen/test", "https://example.invalid", max_tokens=4096)
    truncated = {"choices": [{"message": {"content": "", "reasoning_content": "still planning"}, "finish_reason": "length"}]}
    good = {"choices": [{"message": {"content": '{"ok":true}'}, "finish_reason": "stop"}]}
    with patch("urllib.request.urlopen", side_effect=[io.BytesIO(json.dumps(truncated).encode()), io.BytesIO(json.dumps(good).encode())]) as call:
        assert ask_json(model, "prompt", lambda x:x, LLMBudget()) == {"ok": True}
        assert call.call_count == 2
        retry = json.loads(call.call_args.args[0].data)
        assert retry["chat_template_kwargs"] == {"enable_thinking": False}
        assert retry["max_tokens"] == 8192


def test_token_exhaustion_stops_if_host_ignores_qwen_thinking_control():
    model = OpenAICompatibleModel("qwen/test", "https://example.invalid", max_tokens=4096)
    truncated = {"choices": [{"message": {"content": ""}, "finish_reason": "length"}]}
    with patch("urllib.request.urlopen", side_effect=lambda *a,**k:io.BytesIO(json.dumps(truncated).encode())) as call:
        assert ask_json(model, "prompt", lambda x:x, LLMBudget()) is None
        assert call.call_count == 2


def test_non_qwen_token_exhaustion_stops_with_one_request():
    class TruncatedModel:
        def complete(self, prompt):
            self.last_call = {"finish_reason": "MAX_TOKENS"}
            return '{"partial":'
    model = TruncatedModel()
    budget = LLMBudget()
    assert ask_json(model, "prompt", lambda x:x, budget) is None
    assert budget.total == 1 and budget.exhausted
