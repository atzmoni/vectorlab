"""Engine-level tests for SVG geometry, transform and color fidelity in the DXF export."""

from __future__ import annotations

import io

import ezdxf
import pytest

from app.dxf import HATCH_LAYER, OUTLINE_LAYER, svg_to_dxf, svg_to_dxf_detailed
from app.geometry import (
    color_layer_name,
    color_to_aci,
    color_to_true_color,
    compose_matrix,
    parse_color,
)
from app.svg_parse import (
    extract_path_infos,
    parse_transform,
    parse_viewbox_rect,
    shape_to_path_data,
)

MM_PER_PX = 25.4 / 96


def wrap(body: str, viewbox: str = "0 0 100 100") -> str:
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{viewbox}">{body}</svg>'


def read_dxf(data: bytes):
    return ezdxf.read(io.StringIO(data.decode("utf-8")))


def entities_of(svg: str, **kwargs):
    data, stats = svg_to_dxf_detailed(svg, **kwargs)
    return list(read_dxf(data).modelspace()), stats


# --- transforms -------------------------------------------------------------


def test_transform_list_applies_right_to_left():
    """``translate(50,0) scale(2)`` scales first, then translates — as SVG specifies."""
    assert parse_transform("translate(50,0) scale(2)") == (2.0, 0.0, 0.0, 2.0, 50.0, 0.0)


@pytest.mark.parametrize(
    ("transform", "point", "expected"),
    [
        ("translate(10,5)", (0.0, 0.0), (10.0, 5.0)),
        ("scale(2,3)", (2.0, 2.0), (4.0, 6.0)),
        ("rotate(90)", (1.0, 0.0), (0.0, 1.0)),
        ("rotate(90 1 0)", (1.0, 0.0), (1.0, 0.0)),  # rotating about its own center is a no-op
        ("skewX(45)", (0.0, 1.0), (1.0, 1.0)),
        ("skewY(45)", (1.0, 0.0), (1.0, 1.0)),
        ("matrix(1,0,0,1,7,8)", (0.0, 0.0), (7.0, 8.0)),
    ],
)
def test_each_transform_primitive_maps_points_correctly(transform, point, expected):
    a, b, c, d, e, f = parse_transform(transform)
    x, y = point
    got = (a * x + c * y + e, b * x + d * y + f)
    assert got == pytest.approx(expected, abs=1e-9)


def test_nested_group_transforms_compose_onto_the_child():
    """An ancestor <g transform> must apply after the child's own transform."""
    svg = wrap(
        '<g transform="translate(10,10)"><g transform="scale(2)">'
        '<path d="M 0 0 L 10 0 L 10 10 Z" fill="#ff0000"/></g></g>'
    )
    (info,) = extract_path_infos(svg)
    assert info["matrix"] == pytest.approx((2.0, 0.0, 0.0, 2.0, 10.0, 10.0))


def test_group_transform_moves_geometry_in_the_dxf():
    """Regression: group transforms used to be dropped, landing geometry at the origin."""
    body = '<path d="M 0 0 L 10 0 L 10 10 Z" fill="none" stroke="#000000"/>'
    plain, _ = entities_of(wrap(body))
    shifted, _ = entities_of(wrap(f'<g transform="translate(40,0)">{body}</g>'))
    plain_x = min(p[0] for p in plain[0].control_points)
    shifted_x = min(p[0] for p in shifted[0].control_points)
    assert shifted_x - plain_x == pytest.approx(40 * MM_PER_PX, abs=1e-6)


def test_compose_matrix_handles_missing_operands():
    ident = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    assert compose_matrix(None, ident) == ident
    assert compose_matrix(ident, None) == ident
    assert compose_matrix(None, None) is None


# --- viewBox ----------------------------------------------------------------


def test_viewbox_origin_is_honored_and_y_is_flipped():
    """DXF y grows upward, so the flip is taken about the viewBox rect, not a bare height."""
    svg = wrap('<path d="M 10 20 L 110 20" fill="none" stroke="#000000"/>', viewbox="10 20 100 50")
    entities, _ = entities_of(svg)
    points = [(round(p[0], 6), round(p[1], 6)) for p in entities[0].control_points]
    # x=10 is the viewBox min-x, so it maps to 0; y=20 is the top, so it maps to full height.
    assert points[0][0] == pytest.approx(0.0, abs=1e-6)
    assert points[0][1] == pytest.approx(50 * MM_PER_PX, abs=1e-6)


def test_parse_viewbox_rect_falls_back_to_width_height():
    svg = '<svg xmlns="http://www.w3.org/2000/svg" width="200px" height="80px"><path d="M0 0"/></svg>'
    assert parse_viewbox_rect(svg) == (0.0, 0.0, 200.0, 80.0)


# --- basic shapes -----------------------------------------------------------


@pytest.mark.parametrize(
    ("fragment", "closed"),
    [
        ('<rect x="1" y="2" width="10" height="5"/>', True),
        ('<rect x="0" y="0" width="10" height="10" rx="2"/>', True),
        ('<circle cx="5" cy="5" r="3"/>', True),
        ('<ellipse cx="5" cy="5" rx="4" ry="2"/>', True),
        ('<polygon points="0,0 10,0 10,10"/>', True),
        ('<polyline points="0,0 10,0 10,10"/>', False),
        ('<line x1="0" y1="0" x2="5" y2="5"/>', False),
    ],
)
def test_basic_shapes_reach_the_dxf_as_geometry(fragment, closed):
    """rect/circle/ellipse/line/polyline/polygon are ordinary geometry, not just <path>."""
    entities, stats = entities_of(wrap(fragment, viewbox="0 0 20 20"))
    assert entities, f"{fragment} produced no DXF entity"
    assert stats["subpaths"] == 1
    assert stats["closed_subpaths" if closed else "open_subpaths"] == 1


@pytest.mark.parametrize(
    "fragment",
    [
        '<rect x="0" y="0" width="0" height="5"/>',
        '<circle cx="5" cy="5" r="0"/>',
        '<ellipse cx="5" cy="5" rx="0" ry="2"/>',
        '<line x1="1" y1="1" x2="1" y2="1"/>',
        '<polygon points="1,1"/>',
    ],
)
def test_degenerate_shapes_are_skipped(fragment):
    assert shape_to_path_data(_element(fragment)) == ""


def _element(fragment: str):
    import xml.etree.ElementTree as ET

    return ET.fromstring(fragment)


def test_rect_corner_radius_is_clamped_to_half_the_side():
    d = shape_to_path_data(_element('<rect x="0" y="0" width="10" height="10" rx="99"/>'))
    assert "A 5 5" in d


def test_rect_ry_alone_is_mirrored_to_rx():
    d = shape_to_path_data(_element('<rect x="0" y="0" width="10" height="10" ry="3"/>'))
    assert "A 3 3" in d


# --- non-rendered content ---------------------------------------------------


@pytest.mark.parametrize("tag", ["defs", "clipPath", "mask", "marker", "symbol", "pattern"])
def test_non_rendered_containers_never_become_cut_paths(tag):
    svg = wrap(f'<{tag}><path d="M 0 0 L 50 50"/></{tag}><path d="M 1 1 L 2 2" stroke="#000000"/>')
    infos = extract_path_infos(svg)
    assert len(infos) == 1
    assert infos[0]["d"] == "M 1 1 L 2 2"


@pytest.mark.parametrize(
    "attrs",
    ['style="display:none"', 'style="visibility:hidden"'],
)
def test_hidden_geometry_is_not_exported(attrs):
    svg = wrap(f'<g {attrs}><path d="M 0 0 L 50 50" stroke="#000000"/></g>')
    assert extract_path_infos(svg) == []


# --- paint / style ----------------------------------------------------------


def test_fill_defaults_to_black_and_stroke_defaults_to_none():
    """SVG initial values — an unstyled path paints black, with no stroke."""
    (info,) = extract_path_infos(wrap('<path d="M 0 0 L 10 10 Z"/>'))
    assert info["fill_is_none"] is False
    assert info["stroke_is_none"] is True


def test_inline_style_beats_class_which_beats_presentation_attribute():
    svg = wrap(
        '<style>.c { fill: #00ff00; }</style>'
        '<path class="c" fill="#ff0000" style="fill:#0000ff" d="M 0 0 L 1 1 Z"/>'
    )
    (info,) = extract_path_infos(svg)
    assert info["fill"] == "#0000ff"


def test_class_rule_beats_presentation_attribute():
    svg = wrap('<style>.c { fill: #00ff00; }</style><path class="c" fill="#ff0000" d="M 0 0 L 1 1 Z"/>')
    (info,) = extract_path_infos(svg)
    assert info["fill"] == "#00ff00"


def test_fill_inherits_from_an_ancestor_group():
    svg = wrap('<g fill="#ff0000"><path d="M 0 0 L 1 1 Z"/></g>')
    (info,) = extract_path_infos(svg)
    assert info["fill"] == "#ff0000"


@pytest.mark.parametrize("value", ["none", "transparent", "url(#grad)"])
def test_unpaintable_fills_are_reported_as_none(value):
    (info,) = extract_path_infos(wrap(f'<path fill="{value}" d="M 0 0 L 1 1 Z"/>'))
    assert info["fill_is_none"] is True


@pytest.mark.parametrize("opacity", ["0", "0%"])
def test_zero_fill_opacity_is_treated_as_unpainted(opacity):
    (info,) = extract_path_infos(wrap(f'<path fill="#ff0000" fill-opacity="{opacity}" d="M 0 0 L 1 1 Z"/>'))
    assert info["fill_is_none"] is True


# --- colors -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("#ff0000", (255, 0, 0)),
        ("#f00", (255, 0, 0)),
        ("#ff0000ff", (255, 0, 0)),
        ("rgb(0,0,255)", (0, 0, 255)),
        ("rgb(50%,0%,0%)", (127, 0, 0)),
        ("rgba(0,255,0,0.5)", (0, 255, 0)),
        ("red", (255, 0, 0)),
        ("NAVY", (0, 0, 128)),
        ("  #00ff00  ", (0, 255, 0)),
    ],
)
def test_parse_color_accepts_every_common_svg_notation(value, expected):
    assert parse_color(value) == expected


@pytest.mark.parametrize("value", ["", "none", "transparent", "currentColor", "url(#g)", "#ggg", "notacolor"])
def test_parse_color_rejects_unpaintable_values(value):
    assert parse_color(value) is None


@pytest.mark.parametrize(
    ("value", "aci"),
    [("red", 1), ("yellow", 2), ("lime", 3), ("cyan", 4), ("blue", 5), ("magenta", 6)],
)
def test_primary_colors_map_to_their_standard_aci_index(value, aci):
    assert color_to_aci(value) == aci


@pytest.mark.parametrize("value", ["#000000", "#050505", "#ffffff", "none", "url(#g)"])
def test_near_black_near_white_and_unpaintable_use_the_foreground_index(value):
    """ACI 7 renders black on white paper and white on a dark background."""
    assert color_to_aci(value) == 7


def test_true_color_preserves_the_exact_24_bit_value():
    assert color_to_true_color("#123456") == 0x123456
    assert color_to_true_color("none") is None


def test_color_layer_names_are_deterministic_per_color():
    assert color_layer_name("#ff0000") == color_layer_name("red") == "COLOR_FF0000"
    assert color_layer_name("none") == OUTLINE_LAYER


# --- DXF output -------------------------------------------------------------


def test_fill_emits_a_hatch_and_an_outline_spline():
    entities, stats = entities_of(wrap('<path d="M 0 0 L 10 0 L 10 10 Z" fill="#ff0000"/>'))
    kinds = sorted(e.dxftype() for e in entities)
    assert kinds == ["HATCH", "SPLINE"]
    assert stats["hatches"] == 1 and stats["splines"] == 1
    assert stats["filled_regions"] == 1


def test_stroke_only_path_emits_one_spline_and_no_hatch():
    entities, stats = entities_of(wrap('<path d="M 0 0 L 10 0" fill="none" stroke="#0000ff"/>'))
    assert [e.dxftype() for e in entities] == ["SPLINE"]
    assert stats["hatches"] == 0
    assert stats["stroked_paths"] == 1


def test_entities_carry_true_color_and_nearest_aci():
    entities, _ = entities_of(wrap('<path d="M 0 0 L 10 0" fill="none" stroke="#ff0000"/>'))
    spline = entities[0]
    assert spline.dxf.true_color == 0xFF0000
    assert spline.dxf.color == 1


def test_each_color_gets_its_own_layer_when_color_layers_are_on():
    svg = wrap(
        '<path d="M 0 0 L 10 0 L 10 10 Z" fill="#ff0000"/>'
        '<path d="M 20 20 L 30 20" fill="none" stroke="#0000ff"/>'
    )
    _, stats = entities_of(svg)
    assert stats["layers"] == ["COLOR_0000FF", "COLOR_FF0000"]


def test_color_layers_can_be_switched_off():
    svg = wrap(
        '<path d="M 0 0 L 10 0 L 10 10 Z" fill="#ff0000"/>'
        '<path d="M 20 20 L 30 20" fill="none" stroke="#0000ff"/>'
    )
    _, stats = entities_of(svg, color_layers=False)
    assert set(stats["layers"]) <= {OUTLINE_LAYER, HATCH_LAYER}


def test_stats_count_subpaths_not_entities():
    """A filled subpath emits two entities; it is still one contour."""
    _, stats = entities_of(wrap('<path d="M 0 0 L 10 0 L 10 10 Z" fill="#ff0000"/>'))
    assert stats["subpaths"] == 1
    assert stats["closed_subpaths"] == 1
    assert stats["entities"] == 2


def test_multiple_subpaths_in_one_path_are_counted_separately():
    svg = wrap('<path d="M 0 0 L 10 0 L 10 10 Z M 20 20 L 30 20 L 30 30 Z" fill="#ff0000"/>')
    _, stats = entities_of(svg)
    assert stats["subpaths"] == 2
    assert stats["closed_subpaths"] == 2


def test_reported_colors_list_every_paint_used():
    svg = wrap(
        '<path d="M 0 0 L 10 0 L 10 10 Z" fill="#ff0000"/>'
        '<path d="M 20 20 L 30 20" fill="none" stroke="#0000ff"/>'
    )
    _, stats = entities_of(svg)
    assert stats["colors"] == ["#ff0000", "#0000ff"]


def test_units_set_the_dxf_insunits_header():
    mm, _ = svg_to_dxf_detailed(wrap('<path d="M 0 0 L 10 0" stroke="#000000" fill="none"/>'), units="mm")
    inch, _ = svg_to_dxf_detailed(wrap('<path d="M 0 0 L 10 0" stroke="#000000" fill="none"/>'), units="in")
    assert read_dxf(mm).header["$INSUNITS"] == 4
    assert read_dxf(inch).header["$INSUNITS"] == 1


def test_invalid_units_are_rejected():
    with pytest.raises(ValueError):
        svg_to_dxf_detailed(wrap('<path d="M 0 0 L 1 1"/>'), units="furlong")


def test_svg_to_dxf_wrapper_still_returns_an_entity_count():
    data, count = svg_to_dxf(wrap('<path d="M 0 0 L 10 0 L 10 10 Z" fill="#ff0000"/>'))
    assert count == 2
    assert len(list(read_dxf(data).modelspace())) == count


def test_export_uses_a_dxf_version_that_carries_true_color():
    """R2004 is the first version with group code 420; R2000 would silently drop it."""
    data, _ = svg_to_dxf_detailed(wrap('<path d="M 0 0 L 10 0" stroke="#000000" fill="none"/>'))
    assert read_dxf(data).dxfversion >= ezdxf.DXF2004


def test_per_color_layers_carry_the_exact_rgb():
    data, _ = svg_to_dxf_detailed(wrap('<path d="M 0 0 L 10 0" fill="none" stroke="#123456"/>'))
    layer = read_dxf(data).layers.get("COLOR_123456")
    assert layer.rgb == (0x12, 0x34, 0x56)


def test_hatch_keeps_the_fill_color():
    data, _ = svg_to_dxf_detailed(wrap('<path d="M 0 0 L 10 0 L 10 10 Z" fill="#ff0000"/>'))
    hatch = next(e for e in read_dxf(data).modelspace() if e.dxftype() == "HATCH")
    assert hatch.dxf.true_color == 0xFF0000
