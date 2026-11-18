"""Tests for object hit testing (point selection).

The same assertions run against both the JSON and TMX versions of a fixed
map in tests/test_data/hit_test, which exercises overlapping draw order,
rotation, layer/group offsets, visibility and every object shape. Additional
unit tests cover edge cases that cannot be expressed in a map file: objects
without IDs, invalid tolerances and degenerate shapes.
"""

import math
import os
from pathlib import Path

import pytest

from pytiled_parser import HitResult, OrderedPair, Size, TileLayer, parse_map
from pytiled_parser.hit_test import (
    _distance_to_polyline,
    _polygon_area,
    hit_test,
)
from pytiled_parser.layer import LayerGroup, ObjectLayer
from pytiled_parser.tiled_map import TiledMap
from pytiled_parser.tiled_object import (
    Ellipse,
    Point,
    Polygon,
    Polyline,
    Rectangle,
    Text,
    Tile,
)

TESTS_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
HIT_TEST_DATA = TESTS_DIR / "test_data" / "hit_test"


@pytest.fixture(params=["json", "tmx"])
def tiled_map(request):
    return parse_map(HIT_TEST_DATA / f"map.{request.param}")


def _ids(results):
    return [result.object_id for result in results]


def _names(results):
    return [result.layer.name for result in results]


# ---------------------------------------------------------------------------
# Overlap order (topmost first) across both fixed map formats
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "point,expected_ids",
    [
        # Three overlapping rectangles; "top down" paints larger y on top.
        ((100, 120), [1, 2, 3]),
        # Same overlap plus the object layer drawn later ("Top Layer").
        ((100, 100), [50, 1, 2, 3]),
        # Only two rectangles reach this point.
        ((95, 95), [50, 2, 3]),
    ],
)
def test_overlap_order(tiled_map, point, expected_ids):
    results = hit_test(tiled_map, OrderedPair(*point))
    assert _ids(results) == expected_ids


def test_manual_draw_order(tiled_map):
    # Manual order: last object in the file is painted last, i.e. on top.
    # Layer offsetx = 10, so local (555, 365) maps to world (565, 365).
    results = hit_test(tiled_map, OrderedPair(565, 365))
    assert _ids(results) == [21, 20]


def test_results_carry_layer_and_id(tiled_map):
    results = hit_test(tiled_map, OrderedPair(100, 120))
    assert all(isinstance(result, HitResult) for result in results)
    assert all(isinstance(result.layer, ObjectLayer) for result in results)
    assert all(result.tiled_object.id == result.object_id for result in results)
    top = results[0]
    assert top.layer.name == "Topdown Objects"
    assert top.object_id == 1
    assert top.tiled_object.name == "rect bottom"


# ---------------------------------------------------------------------------
# Rectangles, negative coordinates and tangency
# ---------------------------------------------------------------------------


def test_rectangle_negative_coordinates(tiled_map):
    assert _ids(hit_test(tiled_map, OrderedPair(-15, -5))) == [5]
    assert _ids(hit_test(tiled_map, OrderedPair(-30, -20))) == [5]
    # Boundary tangent counts as a hit.
    assert _ids(hit_test(tiled_map, OrderedPair(0, -20))) == [5]
    # Just past the boundary is a miss.
    assert hit_test(tiled_map, OrderedPair(0.5, -20)) == []


def test_degenerate_rectangle_horizontal_segment(tiled_map):
    # Exact hit on the zero-height segment.
    assert _ids(hit_test(tiled_map, OrderedPair(220, 200))) == [6]
    # One pixel away with no tolerance is a miss.
    assert hit_test(tiled_map, OrderedPair(220, 201)) == []
    # Line tolerance brings it back; point tolerance must not help a segment.
    assert _ids(hit_test(tiled_map, OrderedPair(220, 201), 1.0, 0.0)) == [6]
    assert hit_test(tiled_map, OrderedPair(220, 201), 0.0, 1.0) == []


def test_degenerate_rectangle_point(tiled_map):
    assert _ids(hit_test(tiled_map, OrderedPair(260, 260))) == [7]
    assert hit_test(tiled_map, OrderedPair(261, 260)) == []
    assert _ids(hit_test(tiled_map, OrderedPair(261, 260), 0.0, 1.0)) == [7]
    # Line tolerance does not widen a zero-size point.
    assert hit_test(tiled_map, OrderedPair(261, 260), 1.0, 0.0) == []


# ---------------------------------------------------------------------------
# Ellipses, including rotation and degenerate shapes
# ---------------------------------------------------------------------------


def test_ellipse_hits_and_tangency(tiled_map):
    assert _ids(hit_test(tiled_map, OrderedPair(320, 100))) == [8]
    # Rightmost point of the circle is on the boundary.
    assert _ids(hit_test(tiled_map, OrderedPair(340, 100))) == [8]
    assert hit_test(tiled_map, OrderedPair(340.5, 100)) == []


def test_rotated_ellipse(tiled_map):
    # A 40x20 ellipse whose box origin is (400, 100), rotated 90 degrees.
    # The unrotated center (local 20, 10) maps back to world (390, 100).
    assert _ids(hit_test(tiled_map, OrderedPair(390, 100))) == [9]
    # Rotated boundary tip and a point strictly inside the rotated shape.
    assert _ids(hit_test(tiled_map, OrderedPair(400, 120))) == [9]
    assert _ids(hit_test(tiled_map, OrderedPair(395, 110))) == [9]
    # This point is outside the rotated shape but inside the unrotated bbox.
    assert hit_test(tiled_map, OrderedPair(400, 110)) == []


def test_degenerate_ellipse(tiled_map):
    assert _ids(hit_test(tiled_map, OrderedPair(300, 260))) == [14]
    assert hit_test(tiled_map, OrderedPair(300, 261)) == []
    assert _ids(hit_test(tiled_map, OrderedPair(300, 261), 0.0, 1.0)) == [14]


# ---------------------------------------------------------------------------
# Polygons
# ---------------------------------------------------------------------------


def test_polygon_interior_boundary_and_outside(tiled_map):
    assert _ids(hit_test(tiled_map, OrderedPair(80, 310))) == [10]
    # Point lying exactly on an edge.
    assert _ids(hit_test(tiled_map, OrderedPair(80, 300))) == [10]
    assert hit_test(tiled_map, OrderedPair(100, 315)) == []


def test_rotated_polygon(tiled_map):
    assert _ids(hit_test(tiled_map, OrderedPair(185, 340))) == [11]
    # Rotated corner tip (boundary) still registers.
    assert _ids(hit_test(tiled_map, OrderedPair(170, 340))) == [11]
    assert hit_test(tiled_map, OrderedPair(169, 340)) == []


# ---------------------------------------------------------------------------
# Polylines and points with tolerances
# ---------------------------------------------------------------------------


def test_polyline_hit_and_tolerance(tiled_map):
    assert _ids(hit_test(tiled_map, OrderedPair(425, 200))) == [12]
    assert _ids(hit_test(tiled_map, OrderedPair(450, 225))) == [12]
    assert hit_test(tiled_map, OrderedPair(425, 202)) == []
    assert _ids(hit_test(tiled_map, OrderedPair(425, 202), 3.0)) == [12]


def test_point_hit_and_tolerance(tiled_map):
    assert _ids(hit_test(tiled_map, OrderedPair(500, 50))) == [13]
    assert hit_test(tiled_map, OrderedPair(501, 50)) == []
    assert hit_test(tiled_map, OrderedPair(501, 50), 1.0) == []
    assert _ids(hit_test(tiled_map, OrderedPair(501, 50), 0.0, 1.0)) == [13]


# ---------------------------------------------------------------------------
# Visibility: hidden objects, hidden layers and hidden groups
# ---------------------------------------------------------------------------


def test_invisible_object_is_skipped(tiled_map):
    # Object 4 overlaps object 3 exactly but is marked invisible.
    results = hit_test(tiled_map, OrderedPair(100, 100))
    assert 4 not in _ids(results)


def test_hidden_layer_and_group_are_skipped(tiled_map):
    assert hit_test(tiled_map, OrderedPair(165, 165)) == []  # hidden object layer
    assert hit_test(tiled_map, OrderedPair(50, 130)) == []  # nested hidden group


def test_opacity_does_not_affect_hits(tiled_map):
    # The manual-order layer has opacity 0.5 yet remains selectable.
    results = hit_test(tiled_map, OrderedPair(565, 365))
    assert _ids(results) == [21, 20]
    assert results[0].layer.opacity == 0.5


# ---------------------------------------------------------------------------
# Layer and group offsets
# ---------------------------------------------------------------------------


def test_group_and_layer_offsets(tiled_map):
    # Group offset (50, 20); the object at local (20, 15) maps to (70, 35).
    results = hit_test(tiled_map, OrderedPair(70, 35))
    assert _ids(results) == [30]
    assert results[0].layer.name == "Group Objects"
    # Same query without the group offset lands outside the object.
    assert hit_test(tiled_map, OrderedPair(20, 15)) == []

    # Manual layer offsetx = 10: local (555, 365) -> world (565, 365).
    results = hit_test(tiled_map, OrderedPair(565, 365))
    assert _names(results) == ["Manual Order", "Manual Order"]
    # Without applying the offset, that same query would miss.
    assert hit_test(tiled_map, OrderedPair(555, 365)) == []


# ---------------------------------------------------------------------------
# Edge cases that need hand-built objects / maps
# ---------------------------------------------------------------------------


def _make_map(layers):
    return TiledMap(
        map_file=Path("map.json"),
        infinite=False,
        layers=layers,
        map_size=Size(10, 10),
        next_layer_id=1,
        next_object_id=1,
        orientation="orthogonal",
        render_order="right-down",
        tiled_version="1.0",
        tile_size=Size(16, 16),
        tilesets={},
        version="1.0",
    )


def _object_layer(name, objects, draw_order="topdown", visible=True):
    return ObjectLayer(
        name=name,
        tiled_objects=objects,
        draw_order=draw_order,
        visible=visible,
    )


def test_object_without_id_reports_none():
    rectangle = Rectangle(
        id=None,
        coordinates=OrderedPair(0, 0),
        size=OrderedPair(10, 10),
    )
    tiled_map = _make_map([_object_layer("Layer", [rectangle])])

    results = hit_test(tiled_map, OrderedPair(5, 5))
    assert len(results) == 1
    assert results[0].object_id is None
    assert results[0].tiled_object is rectangle


def test_invalid_tolerances():
    tiled_map = _make_map([_object_layer("Layer", [])])

    for bad_value in (-1, -0.5, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            hit_test(tiled_map, OrderedPair(0, 0), bad_value, 0.0)
        with pytest.raises(ValueError):
            hit_test(tiled_map, OrderedPair(0, 0), 0.0, bad_value)

    for bad_value in ("5", [1], None, True):
        with pytest.raises(TypeError):
            hit_test(tiled_map, OrderedPair(0, 0), bad_value, 0.0)
        with pytest.raises(TypeError):
            hit_test(tiled_map, OrderedPair(0, 0), 0.0, bad_value)


def test_invalid_points():
    rectangle = Rectangle(
        id=1,
        coordinates=OrderedPair(0, 0),
        size=OrderedPair(10, 10),
    )
    tiled_map = _make_map([_object_layer("Layer", [rectangle])])

    with pytest.raises(TypeError):
        hit_test(tiled_map, (1,))
    with pytest.raises(TypeError):
        hit_test(tiled_map, "1,2")
    with pytest.raises(TypeError):
        hit_test(tiled_map, ("1", 2))
    with pytest.raises(ValueError):
        hit_test(tiled_map, (float("nan"), 2))
    with pytest.raises(ValueError):
        hit_test(tiled_map, (1, float("inf")))


def test_degenerate_polygon_uses_line_tolerance():
    # Collinear points form a zero-area polygon, selectable only by outline.
    polygon = Polygon(
        id=1,
        coordinates=OrderedPair(0, 0),
        points=[
            OrderedPair(0, 0),
            OrderedPair(10, 0),
            OrderedPair(20, 0),
        ],
    )
    tiled_map = _make_map([_object_layer("Layer", [polygon])])

    assert _ids(hit_test(tiled_map, OrderedPair(10, 0))) == [1]
    assert hit_test(tiled_map, OrderedPair(10, 1)) == []
    assert _ids(hit_test(tiled_map, OrderedPair(10, 1), 1.0)) == [1]


def test_single_vertex_shapes_use_point_tolerance():
    polygon = Polygon(
        id=1,
        coordinates=OrderedPair(0, 0),
        points=[OrderedPair(5, 5)],
    )
    polyline = Polyline(
        id=2,
        coordinates=OrderedPair(0, 0),
        points=[OrderedPair(5, 5)],
    )
    tiled_map = _make_map([_object_layer("Layer", [polygon, polyline])])

    assert hit_test(tiled_map, OrderedPair(6, 5)) == []
    results = hit_test(tiled_map, OrderedPair(6, 5), 0.0, 1.5)
    assert _ids(results) == [2, 1]
    # Line tolerance alone cannot reach a single vertex.
    assert hit_test(tiled_map, OrderedPair(6, 5), 1.5, 0.0) == []


def test_empty_polygon_and_polyline_never_hit():
    polygon = Polygon(id=1, coordinates=OrderedPair(0, 0), points=[])
    polyline = Polyline(id=2, coordinates=OrderedPair(0, 0), points=[])
    tiled_map = _make_map([_object_layer("Layer", [polygon, polyline])])

    assert hit_test(tiled_map, OrderedPair(0, 0), 100.0, 100.0) == []


def test_rotated_rectangle_hit():
    # A 20x10 rectangle rotated 90 degrees around its origin.
    rectangle = Rectangle(
        id=1,
        coordinates=OrderedPair(50, 50),
        size=OrderedPair(20, 10),
        rotation=90,
    )
    tiled_map = _make_map([_object_layer("Layer", [rectangle])])

    # World (50, 60) maps to local (10, 0): inside the rotated shape.
    assert _ids(hit_test(tiled_map, OrderedPair(50, 60))) == [1]
    # World (60, 50) maps to local (0, -10): outside.
    assert hit_test(tiled_map, OrderedPair(60, 50)) == []


def test_text_and_tile_use_bounding_rectangle():
    text = Text(
        id=1,
        coordinates=OrderedPair(0, 0),
        size=OrderedPair(10, 10),
        text="hello",
    )
    tile = Tile(
        id=2,
        coordinates=OrderedPair(20, 0),
        size=OrderedPair(10, 10),
        gid=1,
    )
    tiled_map = _make_map([_object_layer("Layer", [text, tile])])

    assert _ids(hit_test(tiled_map, OrderedPair(5, 5))) == [1]
    assert _ids(hit_test(tiled_map, OrderedPair(25, 5))) == [2]
    assert hit_test(tiled_map, OrderedPair(15, 5)) == []


def test_rotated_point_ignores_rotation():
    point = Point(
        id=1,
        coordinates=OrderedPair(30, 30),
        rotation=45,
    )
    tiled_map = _make_map([_object_layer("Layer", [point])])

    assert _ids(hit_test(tiled_map, OrderedPair(30, 30))) == [1]
    assert hit_test(tiled_map, OrderedPair(31, 30)) == []
    assert _ids(hit_test(tiled_map, OrderedPair(31, 30), 0.0, 1.0)) == [1]


def test_nested_group_offsets_accumulate():
    inner = _object_layer(
        "Inner Objects",
        [
            Rectangle(
                id=1,
                coordinates=OrderedPair(0, 0),
                size=OrderedPair(10, 10),
            )
        ],
    )
    inner_group = LayerGroup(
        name="Inner Group",
        layers=[inner],
        offset=OrderedPair(5, 5),
    )
    outer_group = LayerGroup(
        name="Outer Group",
        layers=[inner_group],
        offset=OrderedPair(10, 10),
    )
    tiled_map = _make_map([outer_group])

    # Accumulated offset is (15, 15): local (5, 5) -> world (20, 20).
    results = hit_test(tiled_map, OrderedPair(20, 20))
    assert _ids(results) == [1]
    assert results[0].layer is inner


def test_invisible_object_inside_visible_layer_is_skipped():
    visible_rect = Rectangle(
        id=1,
        coordinates=OrderedPair(0, 0),
        size=OrderedPair(10, 10),
    )
    invisible_rect = Rectangle(
        id=2,
        coordinates=OrderedPair(0, 0),
        size=OrderedPair(10, 10),
        visible=False,
    )
    tiled_map = _make_map([_object_layer("Layer", [visible_rect, invisible_rect])])

    assert _ids(hit_test(tiled_map, OrderedPair(5, 5))) == [1]


def test_topdown_tie_keeps_file_order():
    # Identical y: stable sort keeps file order; last file object is on top.
    first = Rectangle(
        id=1,
        coordinates=OrderedPair(0, 0),
        size=OrderedPair(20, 20),
    )
    second = Rectangle(
        id=2,
        coordinates=OrderedPair(0, 0),
        size=OrderedPair(20, 20),
    )
    tiled_map = _make_map([_object_layer("Layer", [first, second])])

    assert _ids(hit_test(tiled_map, OrderedPair(10, 10))) == [2, 1]


def test_hit_on_empty_map():
    tiled_map = _make_map([])
    assert hit_test(tiled_map, OrderedPair(0, 0)) == []


def test_tile_layers_are_ignored():
    # Only object layers can answer hit queries; other layer types are skipped.
    tile_layer = TileLayer(name="Tiles", size=Size(2, 2), data=[[0, 0], [0, 0]])
    object_layer = _object_layer(
        "Objects",
        [Rectangle(id=1, coordinates=OrderedPair(0, 0), size=OrderedPair(10, 10))],
    )
    tiled_map = _make_map([tile_layer, object_layer])

    assert _ids(hit_test(tiled_map, OrderedPair(5, 5))) == [1]


def test_one_dimensional_ellipse_uses_line_tolerance():
    # A 30x0 ellipse collapses to a horizontal segment.
    ellipse = Ellipse(
        id=1,
        coordinates=OrderedPair(0, 0),
        size=OrderedPair(30, 0),
    )
    tiled_map = _make_map([_object_layer("Layer", [ellipse])])

    assert _ids(hit_test(tiled_map, OrderedPair(15, 0))) == [1]
    assert hit_test(tiled_map, OrderedPair(15, 1)) == []
    assert _ids(hit_test(tiled_map, OrderedPair(15, 1), 1.0)) == [1]


def test_polyline_with_duplicate_points():
    # A zero-length segment between two identical points must not divide by zero.
    polyline = Polyline(
        id=1,
        coordinates=OrderedPair(0, 0),
        points=[
            OrderedPair(0, 0),
            OrderedPair(0, 0),
            OrderedPair(10, 0),
        ],
    )
    tiled_map = _make_map([_object_layer("Layer", [polyline])])

    assert _ids(hit_test(tiled_map, OrderedPair(5, 0))) == [1]
    assert hit_test(tiled_map, OrderedPair(5, 1)) == []


def test_polygon_with_two_vertices_is_segment_like():
    polygon = Polygon(
        id=1,
        coordinates=OrderedPair(0, 0),
        points=[OrderedPair(0, 0), OrderedPair(10, 0)],
    )
    tiled_map = _make_map([_object_layer("Layer", [polygon])])

    assert _ids(hit_test(tiled_map, OrderedPair(5, 0))) == [1]
    assert hit_test(tiled_map, OrderedPair(5, 1)) == []
    assert _ids(hit_test(tiled_map, OrderedPair(5, 1), 1.0)) == [1]


def test_distance_helpers_handle_degenerate_inputs():
    origin = OrderedPair(0, 0)

    # Empty polyline is infinitely far away.
    assert math.isinf(_distance_to_polyline(origin, []))
    # A single vertex is measured point-to-point.
    assert _distance_to_polyline(OrderedPair(3, 4), [origin]) == 5.0
    # Polygons with fewer than three vertices have no area.
    assert _polygon_area([]) == 0.0
    assert _polygon_area([origin, OrderedPair(1, 0)]) == 0.0


def test_polyline_negative_coordinates():
    polyline = Polyline(
        id=1,
        coordinates=OrderedPair(-20, -20),
        points=[OrderedPair(0, 0), OrderedPair(10, 10)],
    )
    tiled_map = _make_map([_object_layer("Layer", [polyline])])

    assert _ids(hit_test(tiled_map, OrderedPair(-15, -15))) == [1]
    assert hit_test(tiled_map, OrderedPair(-14, -15)) == []
    assert _ids(hit_test(tiled_map, OrderedPair(-14, -15), 1.0)) == [1]
