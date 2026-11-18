"""Tests for the visible image layer placement query.

These tests exercise ``pytiled_parser.visible_image_layer_placements`` against
both the TMX and JSON versions of the same nested-group background fixture,
plus a set of unit tests covering the edge cases the query is required to
handle deterministically.
"""

import os
from pathlib import Path

import pytest

from pytiled_parser import (
    ImageLayer,
    OrderedPair,
    Rect,
    Size,
    TiledMap,
    parse_map,
    visible_image_layer_placements,
)
from pytiled_parser.layer import LayerGroup

TESTS_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
TEST_DATA = TESTS_DIR / "test_data"
IMAGE_LAYER_TESTS = TEST_DATA / "image_layer_tests"
NESTED_BACKGROUND = IMAGE_LAYER_TESTS / "nested_background"

# The pixel sizes of the images referenced by the nested background fixture.
# The query never loads the images itself; this dict stands in for the image
# size query an application would provide.
FIXTURE_IMAGE_SIZES = {
    Path("images/far_mountains.png"): Size(100, 80),
    Path("images/mid_hills.png"): Size(60, 60),
    Path("images/near_trees.png"): Size(200, 40),
    Path("images/empty.png"): Size(0, 0),
}


def fixture_size_query(image_path):
    return FIXTURE_IMAGE_SIZES[image_path]


def layer_by_name(tiled_map, name):
    """Find an image layer anywhere in the layer tree by name."""
    for layer in tiled_map.layers:
        if isinstance(layer, LayerGroup):
            found = _search_group(layer, name)
            if found is not None:
                return found
        elif isinstance(layer, ImageLayer) and layer.name == name:
            return layer
    raise KeyError(name)


def _search_group(group, name):
    for layer in group.layers or []:
        if isinstance(layer, LayerGroup):
            found = _search_group(layer, name)
            if found is not None:
                return found
        elif isinstance(layer, ImageLayer) and layer.name == name:
            return layer
    return None


@pytest.mark.parametrize("map_file", ["map.json", "map.tmx"])
def test_fixture_parses_and_references_fixture_images(map_file):
    """The original parsing path still works for the new fixtures."""
    tiled_map = parse_map(NESTED_BACKGROUND / map_file)

    assert tiled_map.parallax_origin == OrderedPair(50, 25)

    far = layer_by_name(tiled_map, "Far Mountains")
    assert far.image == Path("images/far_mountains.png")
    assert far.repeat_x is True
    assert far.repeat_y is False
    assert far.parallax_factor == OrderedPair(0.25, 0.5)
    assert far.offset == OrderedPair(10, 20)

    mid = layer_by_name(tiled_map, "Mid Hills")
    assert mid.image == Path("images/mid_hills.png")
    assert mid.repeat_x is True
    assert mid.repeat_y is True

    hidden = layer_by_name(tiled_map, "Hidden Duplicate")
    assert hidden.visible is False


@pytest.mark.parametrize("map_file", ["map.json", "map.tmx"])
def test_placement_counts_and_order(map_file):
    tiled_map = parse_map(NESTED_BACKGROUND / map_file)

    camera = OrderedPair(-30, -10)
    viewport = Rect(-30, -10, 160, 120)

    placements = visible_image_layer_placements(
        tiled_map, camera, viewport, fixture_size_query
    )

    # Three visible Far Mountains repetitions, nine visible Mid Hills
    # repetitions (3 x 3 grid). Near Trees is fully off the viewport, the
    # Hidden Duplicate is invisible and Zero Size has no pixels.
    assert len(placements) == 12

    names = [placement.layer.name for placement in placements]
    assert names == ["Far Mountains"] * 3 + ["Mid Hills"] * 9

    # Placements are grouped by layer, with X varying fastest within a layer.
    far_indices = [(p.repeat_index_x, p.repeat_index_y) for p in placements[:3]]
    assert far_indices == [(-1, 0), (0, 0), (1, 0)]

    mid_indices = [(p.repeat_index_x, p.repeat_index_y) for p in placements[3:]]
    expected_mid = [(x, y) for y in (-2, -1, 0) for x in (-3, -2, -1)]
    assert mid_indices == expected_mid

    # The result is stable across repeated calls.
    again = visible_image_layer_placements(
        tiled_map, camera, viewport, fixture_size_query
    )
    assert again == placements


@pytest.mark.parametrize("map_file", ["map.json", "map.tmx"])
def test_placement_geometry_far_mountains(map_file):
    tiled_map = parse_map(NESTED_BACKGROUND / map_file)

    # View center is (50, 50). With origin (50, 25), parallax factor
    # (0.25, 0.5) and offset (10, 20):
    #   base_x = 10 + (1 - 0.25) * (50 - 50) = 10
    #   base_y = 20 + (1 - 0.5)  * (50 - 25) = 32.5
    placements = visible_image_layer_placements(
        tiled_map,
        OrderedPair(-30, -10),
        Rect(-30, -10, 160, 120),
        fixture_size_query,
    )
    far = [p for p in placements if p.layer.name == "Far Mountains"]

    assert far[0].target_rect == Rect(-90, 32.5, 100, 80)
    assert far[0].viewport_clip_rect == Rect(-30, 32.5, 40, 77.5)
    assert far[0].source_rect == Rect(60, 0, 40, 77.5)

    assert far[1].target_rect == Rect(10, 32.5, 100, 80)
    assert far[1].viewport_clip_rect == Rect(10, 32.5, 100, 77.5)
    assert far[1].source_rect == Rect(0, 0, 100, 77.5)

    assert far[2].target_rect == Rect(110, 32.5, 100, 80)
    assert far[2].viewport_clip_rect == Rect(110, 32.5, 20, 77.5)
    assert far[2].source_rect == Rect(0, 0, 20, 77.5)

    for placement in far:
        assert placement.image == Path("images/far_mountains.png")
        assert placement.effective_opacity == 1.0
        # No Y repetition means the Y index is always zero.
        assert placement.repeat_index_y == 0


@pytest.mark.parametrize("map_file", ["map.json", "map.tmx"])
def test_nested_group_offset_parallax_and_opacity(map_file):
    tiled_map = parse_map(NESTED_BACKGROUND / map_file)

    placements = visible_image_layer_placements(
        tiled_map,
        OrderedPair(-30, -10),
        Rect(-30, -10, 160, 120),
        fixture_size_query,
    )
    mid = [p for p in placements if p.layer.name == "Mid Hills"]

    # Inherited state through Scenery Group -> Mid Hills:
    #   total offset = (100+30, 50+40) = (130, 90)
    #   effective parallax = (0.5*0.5, 2*0.75) = (0.25, 1.5)
    #   effective opacity = 0.5 * 0.8 = 0.4
    # View center (50, 50), origin (50, 25):
    #   base_x = 130 + 0.75 * 0  = 130
    #   base_y = 90 + (-0.5) * 25 = 77.5
    first = next(p for p in mid if p.repeat_index_x == -3 and p.repeat_index_y == 0)
    assert first.target_rect == Rect(-50, 77.5, 60, 60)
    assert first.viewport_clip_rect == Rect(-30, 77.5, 40, 32.5)
    assert first.source_rect == Rect(20, 0, 40, 32.5)

    for placement in mid:
        assert placement.effective_opacity == pytest.approx(0.4)
        assert placement.image == Path("images/mid_hills.png")


@pytest.mark.parametrize("map_file", ["map.json", "map.tmx"])
def test_invisible_and_zero_size_and_offscreen_layers_skipped(map_file):
    tiled_map = parse_map(NESTED_BACKGROUND / map_file)

    placements = visible_image_layer_placements(
        tiled_map,
        OrderedPair(-30, -10),
        Rect(-30, -10, 160, 120),
        fixture_size_query,
    )
    names = {placement.layer.name for placement in placements}
    assert "Hidden Duplicate" not in names
    assert "Zero Size" not in names
    assert "Near Trees" not in names


@pytest.mark.parametrize("map_file", ["map.json", "map.tmx"])
def test_negative_camera_coordinates_and_fractional_parallax(map_file):
    tiled_map = parse_map(NESTED_BACKGROUND / map_file)

    # A camera far into negative territory still produces a finite, fully
    # covering set of repetitions rather than running away to negative index
    # infinity. The viewport is centered around the origin so that the
    # non-vertically-repeating Far Mountains layer remains visible.
    camera = OrderedPair(-500, -120)
    viewport = Rect(-500, -120, 320, 240)

    placements = visible_image_layer_placements(
        tiled_map, camera, viewport, fixture_size_query
    )

    far = [p for p in placements if p.layer.name == "Far Mountains"]
    # The viewport is 320px wide and each image is 100px, so at least four
    # repetitions are required; the parallax shifted grid may demand five.
    assert 4 <= len(far) <= 5
    # All Far placements share the single, non-repeated Y index.
    assert all(p.repeat_index_y == 0 for p in far)
    # The X repetitions must together cover the full viewport width.
    far_edges = sorted(
        (p.viewport_clip_rect.x, p.viewport_clip_rect.x + p.viewport_clip_rect.width)
        for p in far
    )
    assert far_edges[0][0] == viewport.x
    assert far_edges[-1][1] == viewport.x + viewport.width
    # X indices are contiguous.
    far_indices = sorted(p.repeat_index_x for p in far)
    assert far_indices == list(range(far_indices[0], far_indices[-1] + 1))
    # Every clip rectangle must lie within the viewport.
    for placement in placements:
        clip = placement.viewport_clip_rect
        assert viewport.x <= clip.x
        assert viewport.y <= clip.y
        assert clip.x + clip.width <= viewport.x + viewport.width
        assert clip.y + clip.height <= viewport.y + viewport.height
        assert clip.width > 0 and clip.height > 0

    # Union of clip rectangles for a fully repeating layer covers the whole
    # viewport width (Mid Hills repeats on both axes).
    mid = [p for p in placements if p.layer.name == "Mid Hills"]
    covered_x = sorted(
        (p.viewport_clip_rect.x, p.viewport_clip_rect.x + p.viewport_clip_rect.width)
        for p in mid
    )
    assert covered_x[0][0] == viewport.x
    assert covered_x[-1][1] == viewport.x + viewport.width


def _make_simple_map(**layer_kwargs):
    image_layer = ImageLayer(
        name="bg",
        image=Path("bg.png"),
        **layer_kwargs,
    )
    return TiledMap(
        map_file=Path("map.json"),
        infinite=False,
        layers=[image_layer],
        map_size=Size(10, 10),
        next_layer_id=2,
        next_object_id=1,
        orientation="orthogonal",
        render_order="right-down",
        tiled_version="1.10",
        tile_size=Size(32, 32),
        tilesets={},
        version="1.10",
    )


def test_invalid_viewport_raises():
    tiled_map = _make_simple_map()
    size_query = lambda _: Size(100, 100)

    for bad_viewport in (
        Rect(0, 0, 0, 100),
        Rect(0, 0, 100, 0),
        Rect(0, 0, -10, 100),
        Rect(0, 0, 100, -10),
    ):
        with pytest.raises(ValueError):
            visible_image_layer_placements(
                tiled_map, OrderedPair(0, 0), bad_viewport, size_query
            )


def test_zero_size_image_never_placed():
    tiled_map = _make_simple_map(repeat_x=True, repeat_y=True)
    placements = visible_image_layer_placements(
        tiled_map,
        OrderedPair(0, 0),
        Rect(0, 0, 100, 100),
        lambda _: Size(0, 0),
    )
    assert placements == []

    # A zero dimension on only one axis also yields nothing.
    placements = visible_image_layer_placements(
        tiled_map,
        OrderedPair(0, 0),
        Rect(0, 0, 100, 100),
        lambda _: Size(50, 0),
    )
    assert placements == []


def test_single_axis_repeat():
    tiled_map = _make_simple_map(repeat_x=True, repeat_y=False)

    placements = visible_image_layer_placements(
        tiled_map,
        OrderedPair(0, 0),
        Rect(0, 0, 250, 100),
        lambda _: Size(100, 50),
    )

    # Three horizontal repetitions, single Y index.
    assert len(placements) == 3
    assert [p.repeat_index_x for p in placements] == [0, 1, 2]
    assert all(p.repeat_index_y == 0 for p in placements)
    # The 50px tall image does not cover the full 100px viewport height,
    # which is correct since Y repetition is disabled.
    assert all(p.viewport_clip_rect.height == 50 for p in placements)


def test_no_repeat_single_placement_and_clipping():
    tiled_map = _make_simple_map(repeat_x=False, repeat_y=False)

    # Image larger than the viewport and overlapping it partially.
    placements = visible_image_layer_placements(
        tiled_map,
        OrderedPair(50, 60),
        Rect(50, 60, 100, 100),
        lambda _: Size(200, 200),
    )
    assert len(placements) == 1
    placement = placements[0]
    assert placement.repeat_index_x == 0
    assert placement.repeat_index_y == 0
    assert placement.target_rect == Rect(0, 0, 200, 200)
    assert placement.viewport_clip_rect == Rect(50, 60, 100, 100)
    assert placement.source_rect == Rect(50, 60, 100, 100)

    # Fully off the viewport yields nothing.
    assert (
        visible_image_layer_placements(
            tiled_map,
            OrderedPair(1000, 1000),
            Rect(1000, 1000, 100, 100),
            lambda _: Size(10, 10),
        )
        == []
    )


def test_image_smaller_than_viewport_covers_when_repeating():
    tiled_map = _make_simple_map(repeat_x=True, repeat_y=True)

    placements = visible_image_layer_placements(
        tiled_map,
        OrderedPair(0, 0),
        Rect(0, 0, 250, 250),
        lambda _: Size(100, 100),
    )

    # A 3x3 grid of 100px images is needed to cover 250px.
    assert len(placements) == 9
    # The corner placements are clipped to the remaining 50px strips.
    corner = next(
        p for p in placements if p.repeat_index_x == 2 and p.repeat_index_y == 2
    )
    assert corner.viewport_clip_rect == Rect(200, 200, 50, 50)
    assert corner.source_rect == Rect(0, 0, 50, 50)


def test_parallax_origin_is_respected():
    tiled_map = _make_simple_map(
        parallax_factor=OrderedPair(0.5, 0.5),
        offset=OrderedPair(0, 0),
    )
    tiled_map.parallax_origin = OrderedPair(20, 40)

    placements = visible_image_layer_placements(
        tiled_map,
        OrderedPair(0, 0),
        Rect(0, 0, 100, 100),
        lambda _: Size(1000, 1000),
    )
    # View center (50, 50), origin (20, 40), factor 0.5:
    #   position = 0 + 0.5 * ((50-20), (50-40)) = (15, 5)
    assert placements[0].target_rect == Rect(15, 5, 1000, 1000)
    assert placements[0].viewport_clip_rect == Rect(15, 5, 85, 95)
    assert placements[0].source_rect == Rect(0, 0, 85, 95)


def test_hidden_parent_group_skips_children():
    child = ImageLayer(name="child", image=Path("bg.png"))
    group = LayerGroup(
        name="hidden group",
        visible=False,
        layers=[child],
    )
    tiled_map = TiledMap(
        map_file=Path("map.json"),
        infinite=False,
        layers=[group],
        map_size=Size(10, 10),
        next_layer_id=2,
        next_object_id=1,
        orientation="orthogonal",
        render_order="right-down",
        tiled_version="1.10",
        tile_size=Size(32, 32),
        tilesets={},
        version="1.10",
    )

    assert (
        visible_image_layer_placements(
            tiled_map,
            OrderedPair(0, 0),
            Rect(0, 0, 100, 100),
            lambda _: Size(50, 50),
        )
        == []
    )


def test_zero_effective_opacity_is_skipped():
    tiled_map = _make_simple_map(opacity=0)
    assert (
        visible_image_layer_placements(
            tiled_map,
            OrderedPair(0, 0),
            Rect(0, 0, 100, 100),
            lambda _: Size(50, 50),
        )
        == []
    )

    # A fully transparent parent group has the same effect.
    child = ImageLayer(name="child", image=Path("bg.png"))
    group = LayerGroup(name="ghost", opacity=0, layers=[child])
    tiled_map.layers = [group]
    assert (
        visible_image_layer_placements(
            tiled_map,
            OrderedPair(0, 0),
            Rect(0, 0, 100, 100),
            lambda _: Size(50, 50),
        )
        == []
    )


def test_tiled_map_convenience_method_matches_function():
    tiled_map = _make_simple_map(repeat_x=True, repeat_y=False)
    camera = OrderedPair(0, 0)
    viewport = Rect(0, 0, 250, 100)
    size_query = lambda _: Size(100, 50)

    assert tiled_map.visible_image_layer_placements(
        camera, viewport, size_query
    ) == visible_image_layer_placements(tiled_map, camera, viewport, size_query)


def test_image_size_query_receives_layer_path():
    tiled_map = _make_simple_map()
    seen = []

    def size_query(image_path):
        seen.append(image_path)
        return Size(10, 10)

    visible_image_layer_placements(
        tiled_map,
        OrderedPair(0, 0),
        Rect(0, 0, 100, 100),
        size_query,
    )
    assert seen == [Path("bg.png")]


def test_touching_edges_do_not_produce_empty_placements():
    # A 100px image ending exactly at the viewport's left edge must not
    # produce a zero-area placement for index 0.
    tiled_map = _make_simple_map(
        offset=OrderedPair(-100, 0),
        repeat_x=True,
        repeat_y=False,
    )
    placements = visible_image_layer_placements(
        tiled_map,
        OrderedPair(0, 0),
        Rect(0, 0, 100, 100),
        lambda _: Size(100, 100),
    )
    # The index 0 image only touches the viewport edge and is excluded; the
    # index 1 image fills the viewport exactly.
    assert [p.repeat_index_x for p in placements] == [1]
    assert all(p.viewport_clip_rect.width > 0 for p in placements)
    assert all(p.viewport_clip_rect.height > 0 for p in placements)
    assert placements[0].viewport_clip_rect == Rect(0, 0, 100, 100)


def test_fractional_parallax_gives_deterministic_indices():
    tiled_map = _make_simple_map(
        parallax_factor=OrderedPair(1 / 3, 2 / 3),
        repeat_x=True,
        repeat_y=True,
    )
    viewport = Rect(7, -13, 33.5, 41.25)
    camera = OrderedPair(7, -13)

    first = visible_image_layer_placements(
        tiled_map, camera, viewport, lambda _: Size(12.5, 12.5)
    )
    second = visible_image_layer_placements(
        tiled_map, camera, viewport, lambda _: Size(12.5, 12.5)
    )
    assert first == second
    for placement in first:
        assert isinstance(placement.repeat_index_x, int)
        assert isinstance(placement.repeat_index_y, int)
        assert placement.viewport_clip_rect.width > 0
        assert placement.viewport_clip_rect.height > 0


def test_tmx_legacy_xy_position_falls_back_to_offset():
    """Old TMX files stored the image layer position in x/y instead of offset."""
    import xml.etree.ElementTree as etree

    from pytiled_parser.parsers.tmx.layer import parse as parse_tmx

    legacy = etree.fromstring(
        '<imagelayer id="1" name="legacy" x="12" y="-7">'
        '<image source="a.png"/></imagelayer>'
    )
    parsed = parse_tmx(legacy, encoding="utf-8")
    assert parsed.offset == OrderedPair(12.0, -7.0)

    # Explicit offsetx/offsety take precedence over the deprecated x/y.
    modern = etree.fromstring(
        '<imagelayer id="1" name="modern" x="99" y="98" '
        'offsetx="5" offsety="6">'
        '<image source="a.png"/></imagelayer>'
    )
    parsed = parse_tmx(modern, encoding="utf-8")
    assert parsed.offset == OrderedPair(5.0, 6.0)

    neither = etree.fromstring(
        '<imagelayer id="1" name="none">' '<image source="a.png"/></imagelayer>'
    )
    parsed = parse_tmx(neither, encoding="utf-8")
    assert parsed.offset == OrderedPair(0, 0)


def test_empty_group_layer_contributes_nothing():
    """A group with no child layers must not affect the result."""
    group = LayerGroup(name="empty group", layers=[])
    image_layer = ImageLayer(name="bg", image=Path("bg.png"), repeat_x=True)
    tiled_map = TiledMap(
        map_file=Path("map.json"),
        infinite=False,
        layers=[group, image_layer],
        map_size=Size(10, 10),
        next_layer_id=2,
        next_object_id=1,
        orientation="orthogonal",
        render_order="right-down",
        tiled_version="1.10",
        tile_size=Size(32, 32),
        tilesets={},
        version="1.10",
    )

    placements = visible_image_layer_placements(
        tiled_map,
        OrderedPair(0, 0),
        Rect(0, 0, 250, 100),
        lambda _: Size(100, 100),
    )
    assert [p.layer.name for p in placements] == ["bg"] * 3


def test_zero_length_axis_guards_repeat_index_calculation():
    """The repeat index helper must handle zero and negative lengths."""
    from pytiled_parser.image_layer import _visible_repeat_indices

    viewport = Rect(0, 0, 100, 100)
    assert _visible_repeat_indices(0.0, 0.0, viewport, "x") == ()
    assert _visible_repeat_indices(0.0, -5.0, viewport, "y") == ()
    # A normal length still returns the covering index range.
    assert _visible_repeat_indices(0.0, 50.0, viewport, "x") == (0, 1, 2)
