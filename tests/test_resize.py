import copy
import json

import pytest

from engine.check.checker import check
from engine.model.model import ImagesUnsupported, ModelUnavailable, NoModel
from engine.resize import resize
from tests.helpers import load_task

LEVELS = ("level1_warmup", "level2_core", "level3_advanced")


class FakeModel:
    """Scripted model. Routes each prompt by what it asks for and records
    every prompt it was sent."""

    def __init__(self, understanding=None, layouts=(), tie="A", accepts_images=False,
                 refuse_images=False):
        self.understanding = understanding
        self.layouts = list(layouts)
        self.tie = tie
        self.accepts_images, self.refuse_images = accepts_images, refuse_images
        self.prompts = {"understand": [], "layout": [], "tie": []}
        self.images = []

    def complete(self, prompt, images=None):
        self.images.append(len(images or ()))
        if images and self.refuse_images:
            raise ImagesUnsupported("this model is text-only")
        if "Two layouts" in prompt:
            self.prompts["tie"].append(prompt)
            return json.dumps({"better": self.tie})
        if "senior graphic designer" in prompt:
            self.prompts["layout"].append(prompt)
            if not self.layouts:
                raise AssertionError("more layouts requested than scripted")
            reply = self.layouts.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return reply
        self.prompts["understand"].append(prompt)
        if self.understanding is None:
            raise ModelUnavailable("not scripted")
        return self.understanding


def understanding_reply(source, difficulty):
    ids = [el["id"] for el in source["elements"]]
    from engine.analyze.breakdown import infer_priorities, infer_roles
    roles = infer_roles(source)
    priorities = infer_priorities(source, roles)
    return json.dumps({
        "colors": {"palette": [{"hex": "#2B1B2E", "name": "plum", "usage": "background"}],
                   "scheme": "Split-Complementary", "notes": "warm orange pops on plum"},
        "pattern": {"source": "centered", "source_notes": "stacked on the axis",
                    "target": "horizontal_flow", "target_notes": "run the stack left to right"},
        "description": "A centred stack under a glowing headline. Orange accents lead to the button.",
        "elements": [{"id": i, "role": roles[i], "priority": priorities[i], "why": f"{i} matters"}
                     for i in ids],
        "relationships": [{"a": "subhead", "relation": "below", "b": "headline"}],
        "difficulty": difficulty})


def layout_reply(layout, summary=None, **changes):
    """A model reply giving `layout`'s geometry, with per-id overrides."""
    elements = []
    for el in layout["elements"]:
        item = {k: el[k] for k in ("id", "x", "y", "width", "height", "font_size") if k in el}
        item.update(changes.get(el["id"], {}))
        elements.append(item)
    reply = {"elements": elements}
    if summary:
        reply.update(pattern="horizontal_flow", summary=summary)
    return json.dumps(reply)


def element(layout, eid):
    return next(el for el in layout["elements"] if el["id"] == eid)


def baseline(level):
    """The preset layout, as returned when no model is available."""
    task = load_task(level)
    return task, resize(task["source"], task["target_canvas"], model=NoModel())


@pytest.mark.parametrize("level", LEVELS)
def test_without_a_model_every_element_is_returned_on_the_target_canvas(level):
    task = load_task(level)
    out = resize(task["source"], task["target_canvas"], model=NoModel())
    assert out["canvas"] == task["target_canvas"]
    assert [el["id"] for el in out["elements"]] == [el["id"] for el in task["source"]["elements"]]
    off = [v.element_id for v in check(task["source"], out, task["target_canvas"]).signals
           if v.kind == "off_canvas"]
    assert off == []


@pytest.mark.parametrize("level", LEVELS)
def test_nothing_is_hidden_and_images_keep_proportions(level):
    task = load_task(level)
    out = resize(task["source"], task["target_canvas"], model=NoModel())
    kinds = {v.kind for v in check(task["source"], out, task["target_canvas"]).rules}
    assert not kinds & {"invisible", "distorted_image", "missing_element", "changed_protected_field"}


def test_core_sample_preset_layout_satisfies_every_rule():
    task, out = baseline("level2_core")
    assert check(task["source"], out, task["target_canvas"]).passes


def test_easy_design_with_passing_preset_never_asks_for_a_layout():
    task = load_task("level1_warmup")
    model = FakeModel(understanding_reply(task["source"], "easy"))
    out = resize(task["source"], task["target_canvas"], model=model)
    assert len(model.prompts["understand"]) == 1
    assert model.prompts["layout"] == []
    assert check(task["source"], out, task["target_canvas"]).passes


def unbreakable_word_task():
    """A design whose preset layout must break a rule: one word too long to
    wrap overflows any box, at low space pressure."""
    task = load_task("level1_warmup")
    for el in task["source"]["elements"]:
        if el["id"] == "cta":
            el["content"] = "W" * 120
    return task


def test_easy_design_whose_preset_breaks_a_rule_is_promoted_to_hard():
    task = unbreakable_word_task()
    preset = resize(task["source"], task["target_canvas"], model=NoModel())
    assert not check(task["source"], preset, task["target_canvas"]).passes
    model = FakeModel(understanding_reply(task["source"], "easy"), layouts=["not json"] * 3)
    resize(task["source"], task["target_canvas"], model=model)
    assert len(model.prompts["layout"]) == 3
    assert "Difficulty: hard" in model.prompts["layout"][0]


def test_high_space_pressure_overrides_an_easy_judgement():
    task = load_task("level3_advanced")
    model = FakeModel(understanding_reply(task["source"], "easy"), layouts=["not json"] * 3)
    resize(task["source"], task["target_canvas"], model=model)
    assert "Difficulty: hard" in model.prompts["layout"][0]


def test_check_loop_stops_at_three_design_attempts():
    task, preset = baseline("level2_core")
    attempts = [layout_reply(preset, product={"x": 1600 - 100 * n}) for n in range(1, 6)]
    model = FakeModel(understanding_reply(task["source"], "hard"), layouts=attempts)
    resize(task["source"], task["target_canvas"], model=model)
    assert len(model.prompts["layout"]) == 3


def test_check_loop_stops_early_when_an_attempt_does_not_improve():
    task, preset = baseline("level2_core")
    attempts = [layout_reply(preset, headline={"font_size": 4}), layout_reply(preset, headline={"font_size": 3}),
                layout_reply(preset)]
    model = FakeModel(understanding_reply(task["source"], "hard"), layouts=attempts)
    resize(task["source"], task["target_canvas"], model=model)
    assert len(model.prompts["layout"]) == 2


def test_violation_report_of_the_previous_attempt_is_sent_back():
    task, preset = baseline("level2_core")
    attempts = [layout_reply(preset, product={"x": 1500}), layout_reply(preset, product={"x": 1400}),
                layout_reply(preset, product={"x": 1300})]
    model = FakeModel(understanding_reply(task["source"], "hard"), layouts=attempts)
    resize(task["source"], task["target_canvas"], model=model)
    second = model.prompts["layout"][1]
    assert "Your previous attempt" in second and "off_canvas" in second and "displacement" in second


def test_preset_layout_is_returned_when_no_attempt_beats_it():
    task, preset = baseline("level2_core")
    attempts = [layout_reply(preset, headline={"font_size": 4})] * 2
    model = FakeModel(understanding_reply(task["source"], "hard"), layouts=attempts)
    out = resize(task["source"], task["target_canvas"], model=model)
    assert element(out, "headline")["x"] == element(preset, "headline")["x"]


def test_an_attempt_tied_on_rules_wins_when_the_llm_prefers_it():
    task, preset = baseline("level2_core")
    # Same rule cost as the preset layout, but it raises a small-text signal.
    attempts = [layout_reply(preset, subhead={"font_size": 9}),
                layout_reply(preset, headline={"font_size": 4})]
    model = FakeModel(understanding_reply(task["source"], "hard"), layouts=attempts, tie="A")
    out = resize(task["source"], task["target_canvas"], model=model)
    assert model.prompts["tie"], "signals tie-break should be asked"
    assert element(out, "subhead")["font_size"] == 9


def test_unusable_replies_are_retried_with_the_mistake_and_do_not_use_up_attempts():
    task, preset = baseline("level2_core")
    attempts = ["sorry, no", json.dumps({"elements": []}),
                layout_reply(preset, product={"x": 1500}),
                layout_reply(preset, product={"x": 1400}),
                layout_reply(preset, product={"x": 1300})]
    model = FakeModel(understanding_reply(task["source"], "hard"), layouts=attempts)
    resize(task["source"], task["target_canvas"], model=model)
    assert len(model.prompts["layout"]) == 5  # 2 LLM failures + 3 design attempts
    assert "Do not repeat this mistake" in model.prompts["layout"][1]
    assert "missing" in model.prompts["layout"][2]


def test_three_unusable_replies_in_a_row_end_the_loop_with_the_preset_layout():
    task, preset = baseline("level2_core")
    model = FakeModel(understanding_reply(task["source"], "hard"), layouts=["{"] * 10)
    out = resize(task["source"], task["target_canvas"], model=model)
    assert len(model.prompts["layout"]) == 3
    assert out == preset


def test_six_unusable_replies_in_total_end_the_loop():
    task, preset = baseline("level2_core")
    bad, good = "{", lambda x: layout_reply(preset, product={"x": x})
    attempts = [bad, bad, good(1500), bad, bad, good(1400), bad, bad, good(1300)]
    model = FakeModel(understanding_reply(task["source"], "hard"), layouts=attempts)
    resize(task["source"], task["target_canvas"], model=model)
    # 2 fails, attempt, 2 fails, attempt, 2 fails: the 6th failure ends it
    assert len(model.prompts["layout"]) == 8


def test_fields_the_llm_may_not_change_are_restored_from_the_source():
    task, preset = baseline("level2_core")
    tampered = json.loads(layout_reply(preset))
    for item in tampered["elements"]:
        item.update(content="HACKED", font_family="Comic Sans", opacity=0, asset_id="x.png")
    model = FakeModel(understanding_reply(task["source"], "hard"), layouts=[json.dumps(tampered)] * 3)
    out = resize(task["source"], task["target_canvas"], model=model)
    for src, el in zip(task["source"]["elements"], out["elements"]):
        for field in ("content", "font_family", "opacity", "asset_id"):
            assert el.get(field) == src.get(field)


def test_a_crashing_model_still_produces_output():
    task, preset = baseline("level2_core")
    model = FakeModel(understanding_reply(task["source"], "hard"),
                      layouts=[RuntimeError("boom")] * 6)
    out = resize(task["source"], task["target_canvas"], model=model)
    assert out == preset


def test_source_design_is_not_mutated():
    task = load_task("level3_advanced")
    before = copy.deepcopy(task["source"])
    resize(task["source"], task["target_canvas"], model=NoModel())
    assert task["source"] == before


# --- Design analysis and the layout model's reasoning --------------------------

def run_traced(task, model, **options):
    from engine.tracing.trace import Trace
    trace = Trace("test")
    out = resize(task["source"], task["target_canvas"], model=model, trace=trace, **options)
    return out, {s["kind"]: s for s in trace.steps}, trace.steps


def test_design_analysis_is_recorded_with_code_measured_technical_facts():
    task = load_task("level2_core")
    model = FakeModel(understanding_reply(task["source"], "easy"))
    _, steps, _ = run_traced(task, model)
    analysis = steps["understand"]["analysis"]
    assert analysis["decided_by"] == "llm"
    assert analysis["color_scheme"] == "split_complementary"
    assert analysis["pattern"] == "centered" and analysis["target_pattern"] == "horizontal_flow"
    assert analysis["description"].startswith("A centred stack")
    assert analysis["priority_reasons"]["headline"] == "headline matters"
    headline = next(t for t in analysis["technical"] if t["id"] == "headline")
    src = next(el for el in task["source"]["elements"] if el["id"] == "headline")
    assert (headline["x"], headline["font_size"]) == (round(src["x"], 1), src["font_size"])


def test_analysis_missing_its_description_is_retried():
    task = load_task("level2_core")
    reply = json.loads(understanding_reply(task["source"], "easy"))
    del reply["description"]
    model = FakeModel(json.dumps(reply))
    _, steps, _ = run_traced(task, model)
    assert steps["understand"]["decided_by"] == "heuristics"
    assert "description" in model.prompts["understand"][1]


def test_the_rendered_source_is_shown_to_a_model_that_accepts_images():
    task = load_task("level2_core")
    model = FakeModel(understanding_reply(task["source"], "easy"), accepts_images=True)
    _, steps, _ = run_traced(task, model, source_image=b"png bytes")
    assert model.images[0] == 1
    assert "attached as an image" in model.prompts["understand"][0]
    assert steps["understand"]["analysis"]["visual_input"] == "image"


def test_a_model_refusing_the_image_falls_back_to_extracted_colours():
    task = load_task("level2_core")
    model = FakeModel(understanding_reply(task["source"], "easy"), accepts_images=True,
                      refuse_images=True)
    _, steps, _ = run_traced(task, model, source_image=b"png bytes")
    assert model.images[:2] == [1, 0]
    assert "Colours measured in code" in model.prompts["understand"][-1]
    analysis = steps["understand"]["analysis"]
    assert analysis["visual_input"] == "palette"
    assert "#E86A3A" in analysis["extracted_colours"]["shape_fills"]


def test_without_extractable_colours_the_analysis_uses_element_facts_only(monkeypatch):
    import engine.analyze.understand as understand_module
    monkeypatch.setattr(understand_module, "extract_palette", lambda source: None)
    task = load_task("level2_core")
    model = FakeModel(understanding_reply(task["source"], "easy"), accepts_images=True,
                      refuse_images=True)
    _, steps, _ = run_traced(task, model, source_image=b"png bytes")
    assert "No image or measured colours" in model.prompts["understand"][-1]
    assert steps["understand"]["analysis"]["visual_input"] == "text"


def test_layout_prompt_carries_the_analysis_and_asks_for_justifications():
    task, preset = baseline("level2_core")
    model = FakeModel(understanding_reply(task["source"], "hard"), layouts=[layout_reply(preset)] * 3)
    resize(task["source"], task["target_canvas"], model=model)
    first = model.prompts["layout"][0]
    assert "A centred stack under a glowing headline" in first
    assert "horizontal_flow" in first and '"why": "headline matters"' in first
    assert '"summary"' in first


def test_each_attempt_reads_back_the_previous_reasoning_and_summaries():
    task, preset = baseline("level2_core")
    first = json.loads(layout_reply(preset, summary="First try: a left-to-right strip.",
                                    product={"x": 1500}))
    for item in first["elements"]:
        item["why"] = f"placed {item['id']} on purpose"
    first["groups_note"] = "ignored extra key"
    attempts = [json.dumps(first),
                layout_reply(preset, summary="Second try: product pulled in.", product={"x": 1400}),
                layout_reply(preset, product={"x": 1300})]
    model = FakeModel(understanding_reply(task["source"], "hard"), layouts=attempts)
    _, _, steps = run_traced(task, model)
    second, third = model.prompts["layout"][1], model.prompts["layout"][2]
    assert "First try: a left-to-right strip." in second
    assert '"why": "placed product on purpose"' in second
    assert "First try" in third and "Second try: product pulled in." in third
    assert "You gave no justification" in third
    recorded = [s["rationale"] for s in steps if s["kind"] == "attempt"]
    assert recorded[0]["summary"] == "First try: a left-to-right strip."
    assert recorded[0]["justifications"]["product"] == "placed product on purpose"


def test_rationale_extractor_tolerates_missing_reasoning_and_maps_groups():
    from engine.resize.rationale import extract_rationale
    source = load_task("level2_core")["source"]
    rationale = extract_rationale(source, {
        "pattern": "split_screen", "summary": "Two halves.",
        "elements": [{"id": "headline", "why": "Leads the copy zone."},
                     {"id": "cta_label", "why": "Button on the right."}],
        "groups": [{"group_id": "badge", "why": ""}]})
    assert rationale.pattern == "split_screen" and rationale.summary == "Two halves."
    assert rationale.justifications["headline"] == "Leads the copy zone."
    cta_group = next(el["group_id"] for el in source["elements"] if el["id"] == "cta_label")
    assert rationale.justifications[cta_group] == "Button on the right."
    assert "product" in rationale.missing and "headline" not in rationale.missing
    assert extract_rationale(source, "not a dict").summary == ""


def test_dates_and_qr_codes_are_recognised_and_ranked_just_below_the_headline():
    from engine.analyze.breakdown import infer_priorities, infer_roles
    source = copy.deepcopy(load_task("level2_core")["source"])
    source["elements"] += [
        {"id": "event_date", "type": "text", "content": "Saturday, June 21", "x": 100, "y": 900,
         "width": 300, "height": 40, "font_size": 24, "font_family": "Inter", "color": "#FFFFFF"},
        {"id": "qr", "type": "image", "asset_id": "event_qr.png", "x": 900, "y": 900,
         "width": 120, "height": 120}]
    roles = infer_roles(source)
    assert roles["event_date"] == "date" and roles["qr"] == "qr_code"
    order = sorted(roles, key=infer_priorities(source, roles).get)
    assert order[:3] == ["headline", "qr", "event_date"]


def test_a_shrunken_qr_code_raises_a_signal():
    source = {"canvas": {"width": 400, "height": 400}, "elements": [
        {"id": "qr", "type": "image", "asset_id": "qr.png", "x": 10, "y": 10, "width": 200, "height": 200}]}
    layout = copy.deepcopy(source)
    layout["elements"][0].update(width=30, height=30)
    report = check(source, layout, {"width": 400, "height": 400})
    assert [v.kind for v in report.signals if v.element_id == "qr"] == ["qr_too_small"]
