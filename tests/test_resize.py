import copy
import json

import pytest

from engine.checker import check
from engine.model import ModelUnavailable, NoModel
from engine.resize import resize
from tests.helpers import load_task

LEVELS = ("level1_warmup", "level2_core", "level3_advanced")


class FakeModel:
    """Scripted model. Routes each prompt by what it asks for and records
    every prompt it was sent."""

    def __init__(self, understanding=None, layouts=(), tie="A"):
        self.understanding = understanding
        self.layouts = list(layouts)
        self.tie = tie
        self.prompts = {"understand": [], "layout": [], "tie": []}

    def complete(self, prompt):
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
    from engine.breakdown import infer_priorities, infer_roles
    roles = infer_roles(source)
    priorities = infer_priorities(source, roles)
    return json.dumps({"elements": [{"id": i, "role": roles[i], "priority": priorities[i]} for i in ids],
                       "difficulty": difficulty})


def layout_reply(layout, **changes):
    """A model reply giving `layout`'s geometry, with per-id overrides."""
    elements = []
    for el in layout["elements"]:
        item = {k: el[k] for k in ("id", "x", "y", "width", "height", "font_size") if k in el}
        item.update(changes.get(el["id"], {}))
        elements.append(item)
    return json.dumps({"elements": elements})


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
    off = [v.element_id for v in check(task["source"], out, task["target_canvas"]).rules
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
    attempts = [layout_reply(preset, product={"x": 1400}), layout_reply(preset, product={"x": 1550}),
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
    attempts = [layout_reply(preset, headline={"x": 1500})] * 2
    model = FakeModel(understanding_reply(task["source"], "hard"), layouts=attempts)
    out = resize(task["source"], task["target_canvas"], model=model)
    assert element(out, "headline")["x"] == element(preset, "headline")["x"]


def test_an_attempt_tied_on_rules_wins_when_the_llm_prefers_it():
    task, preset = baseline("level2_core")
    # Same rule cost as the preset layout, but it raises a small-text signal.
    attempts = [layout_reply(preset, subhead={"font_size": 9}),
                layout_reply(preset, product={"x": 1500})]
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
