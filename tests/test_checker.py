import copy

from engine.check.checker import check
from tests.helpers import load_task

SQUARE = {"width": 1080, "height": 1080}


def kinds(violations):
    return {v.kind for v in violations}


def layout_of(source, canvas=SQUARE):
    return {"canvas": dict(canvas), "elements": copy.deepcopy(source["elements"])}


def element(layout, eid):
    return next(el for el in layout["elements"] if el["id"] == eid)


def core_source():
    return load_task("level2_core")["source"]


def in_canvas(source):
    """The core sample's decorations bleed off the canvas on purpose; pull
    them in so a test starts from a layout that passes every rule."""
    layout = layout_of(source)
    for eid in ("deco_circle_1",):
        el = element(layout, eid)
        el["x"], el["y"] = 0, 0
    return layout


def test_unchanged_in_canvas_layout_passes():
    source = core_source()
    report = check(source, in_canvas(source), SQUARE)
    assert report.passes, report.to_dict()


def test_missing_extra_and_duplicate_elements_are_rules():
    source = core_source()
    layout = in_canvas(source)
    layout["elements"] = [el for el in layout["elements"] if el["id"] != "logo"]
    layout["elements"].append(copy.deepcopy(element(layout, "headline")))
    layout["elements"].append({**element(layout, "subhead"), "id": "invented"})
    report = check(source, layout, SQUARE)
    assert {"missing_element", "duplicate_element", "extra_element"} <= kinds(report.rules)


def test_zero_opacity_is_a_rule():
    source = core_source()
    layout = in_canvas(source)
    element(layout, "subhead")["opacity"] = 0
    report = check(source, layout, SQUARE)
    assert "invisible" in kinds(report.rules)


def test_off_canvas_is_a_rule_and_source_bleed_counts():
    source = core_source()
    report = check(source, layout_of(source), SQUARE)
    off = [v for v in report.rules if v.kind == "off_canvas"]
    assert [v.element_id for v in off] == ["deco_circle_1"]


def test_rotation_is_included_in_off_canvas_bounds():
    source = load_task("level3_advanced")["source"]
    canvas = source["canvas"]
    layout = layout_of(source, canvas)
    ribbon = [element(layout, "ribbon_bg"), element(layout, "ribbon_label")]
    for el in ribbon:  # unrotated these would just touch the right edge
        el["x"] += canvas["width"] - (ribbon[0]["x"] + ribbon[0]["width"])
    report = check(source, layout, canvas)
    assert "ribbon_bg" in {v.element_id for v in report.rules if v.kind == "off_canvas"}


def test_image_proportion_tolerance_and_cover_exemption():
    source = core_source()
    layout = in_canvas(source)
    element(layout, "product")["width"] *= 1.04  # within 5%
    assert "distorted_image" not in kinds(check(source, layout, SQUARE).rules)

    element(layout, "product")["width"] *= 1.10
    element(layout, "bg")["width"] = 400  # cover background may crop
    distorted = [v.element_id for v in check(source, layout, SQUARE).rules if v.kind == "distorted_image"]
    assert distorted == ["product"]


def test_text_overflow_is_measured_with_real_fonts():
    source = core_source()
    layout = in_canvas(source)
    element(layout, "subhead")["width"] = 200  # wraps to many lines in a 70px box
    report = check(source, layout, SQUARE)
    assert "subhead" in {v.element_id for v in report.rules if v.kind == "text_overflow"}


def test_font_floors_six_is_a_rule_ten_is_a_signal():
    source = core_source()
    layout = in_canvas(source)
    element(layout, "subhead")["font_size"] = 8
    element(layout, "badge_label")["font_size"] = 5
    report = check(source, layout, SQUARE)
    assert "subhead" in {v.element_id for v in report.signals if v.kind == "small_text"}
    assert "badge_label" in {v.element_id for v in report.rules if v.kind == "text_below_floor"}


def test_legal_text_may_go_to_six_px():
    source = load_task("level3_advanced")["source"]
    canvas = source["canvas"]
    layout = layout_of(source, canvas)
    element(layout, "legal_text")["font_size"] = 6.5
    report = check(source, layout, canvas)
    assert "legal_text" not in {v.element_id for v in report.signals + report.rules
                                if v.kind in ("small_text", "text_below_floor")}


def test_moving_a_group_together_is_fine_but_splitting_it_is_a_rule():
    source = core_source()
    layout = in_canvas(source)
    for eid in ("cta_bg", "cta_label"):
        element(layout, eid)["y"] -= 300
    assert "group_broken" not in kinds(check(source, layout, SQUARE).rules)

    element(layout, "cta_label")["x"] += 150
    assert "group_broken" in kinds(check(source, layout, SQUARE).rules)


def test_new_overlap_is_a_signal_but_source_layering_and_decoration_are_not():
    source = core_source()
    layout = in_canvas(source)
    report = check(source, layout, SQUARE)
    assert "overlap" not in kinds(report.signals)  # subhead/cta overlap already in source

    element(layout, "headline")["y"] = element(layout, "product")["y"] + 100
    report = check(source, layout, SQUARE)
    assert "overlap" in kinds(report.signals)
    assert report.passes  # a signal never rejects a layout


def test_changing_protected_fields_is_a_rule():
    source = core_source()
    layout = in_canvas(source)
    element(layout, "headline")["content"] = "Chase it"
    assert "changed_protected_field" in kinds(check(source, layout, SQUARE).rules)


def test_violations_carry_priority_and_displacement():
    source = core_source()
    layout = in_canvas(source)
    element(layout, "product")["x"] = 2000
    off = next(v for v in check(source, layout, SQUARE).rules if v.kind == "off_canvas")
    assert off.priority is not None and off.displacement > 0.5


def test_rule_cost_weights_higher_priority_elements_more():
    source = core_source()
    a, b = in_canvas(source), in_canvas(source)
    element(a, "headline")["opacity"] = 0  # top priority, same severity
    element(b, "subhead")["opacity"] = 0   # lower priority
    assert check(source, a, SQUARE).rule_cost > check(source, b, SQUARE).rule_cost
