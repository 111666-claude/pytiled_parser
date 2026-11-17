"""Tests for World rectangular region queries and on-demand map loading."""

import os
from pathlib import Path

import pytest

from pytiled_parser import TiledMap, World, WorldMap, common_types, world

TESTS_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
TEST_DATA = TESTS_DIR / "test_data"
WORLD_TESTS = TEST_DATA / "world_tests"
BOTH_WORLD = WORLD_TESTS / "both" / "world.world"
STATIC_WORLD = WORLD_TESTS / "static_defined" / "world.world"
INVALID_SIZE_WORLD = WORLD_TESTS / "invalid_size" / "world.world"


def make_world_map(
    map_file: Path,
    x: float,
    y: float,
    width: float = 100.0,
    height: float = 100.0,
) -> WorldMap:
    return WorldMap(
        map_file=map_file,
        size=common_types.Size(width, height),
        coordinates=common_types.OrderedPair(x, y),
    )


# ---------------------------------------------------------------------------
# maps_in_region: basic hits, negative coordinates, float positions
# ---------------------------------------------------------------------------


def test_region_hit_single_map():
    wm = make_world_map(Path("a.json"), 0, 0)
    result = World(maps=[wm]).maps_in_region(0, 0, 100, 100)
    assert result == [wm]


def test_region_miss_returns_empty_list():
    wm = make_world_map(Path("a.json"), 0, 0)
    result = World(maps=[wm]).maps_in_region(200, 200, 50, 50)
    assert result == []


def test_region_negative_coordinates():
    # A map occupying [-100, 0] on both axes.
    wm = make_world_map(Path("a.json"), -100, -100)
    world_obj = World(maps=[wm])

    # A viewport entirely in negative space overlapping the map.
    assert world_obj.maps_in_region(-150, -150, 75, 75) == [wm]
    # A viewport crossing the origin.
    assert world_obj.maps_in_region(-50, -50, 100, 100) == [wm]
    # Fully positive viewport should miss it.
    assert world_obj.maps_in_region(10, 10, 20, 20) == []


def test_region_float_positions_and_sizes():
    wm = make_world_map(Path("a.json"), 0.5, 0.25, 10.5, 20.75)
    world_obj = World(maps=[wm])

    assert world_obj.maps_in_region(0.1, 0.1, 1.0, 1.0) == [wm]
    assert world_obj.maps_in_region(10.9, 20.9, 0.05, 0.05) == [wm]
    assert world_obj.maps_in_region(11.0, 21.0, 1.0, 1.0) == []


def test_region_partial_overlap_on_each_side():
    wm = make_world_map(Path("a.json"), 0, 0)
    world_obj = World(maps=[wm])

    # Overlapping from the left, right, top and bottom respectively.
    assert world_obj.maps_in_region(-50, 25, 100, 50) == [wm]
    assert world_obj.maps_in_region(50, 25, 100, 50) == [wm]
    assert world_obj.maps_in_region(25, -50, 50, 100) == [wm]
    assert world_obj.maps_in_region(25, 50, 50, 100) == [wm]
    # Region fully containing the map.
    assert world_obj.maps_in_region(-10, -10, 120, 120) == [wm]
    # Region fully contained by the map.
    assert world_obj.maps_in_region(25, 25, 50, 50) == [wm]


# ---------------------------------------------------------------------------
# maps_in_region: boundary tangency is strict (half-open intervals)
# ---------------------------------------------------------------------------


def test_region_edge_tangency_does_not_intersect():
    wm = make_world_map(Path("a.json"), 0, 0)
    world_obj = World(maps=[wm])

    # Sharing exactly one edge.
    assert world_obj.maps_in_region(100, 0, 100, 100) == []
    assert world_obj.maps_in_region(-100, 0, 100, 100) == []
    assert world_obj.maps_in_region(0, 100, 100, 100) == []
    assert world_obj.maps_in_region(0, -100, 100, 100) == []


def test_region_corner_tangency_does_not_intersect():
    wm = make_world_map(Path("a.json"), 0, 0)
    world_obj = World(maps=[wm])

    assert world_obj.maps_in_region(100, 100, 50, 50) == []
    assert world_obj.maps_in_region(-50, -50, 50, 50) == []


def test_region_one_pixel_overlap_does_intersect():
    wm = make_world_map(Path("a.json"), 0, 0)
    world_obj = World(maps=[wm])

    assert world_obj.maps_in_region(99, 99, 50, 50) == [wm]


# ---------------------------------------------------------------------------
# maps_in_region: zero / negative viewport dimensions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "width, height",
    [
        (0, 100),
        (100, 0),
        (0, 0),
        (-1, 100),
        (100, -1),
        (-10, -10),
    ],
)
def test_region_invalid_viewport_dimensions_raise(width, height):
    world_obj = World(maps=[make_world_map(Path("a.json"), 0, 0)])
    with pytest.raises(ValueError) as exc_info:
        world_obj.maps_in_region(0, 0, width, height)
    message = str(exc_info.value)
    # The error must carry enough context to identify the bad argument.
    assert "greater than zero" in message
    assert repr(width) in message and repr(height) in message


def test_region_invalid_viewport_raises_before_checking_maps():
    # Even an empty world must reject a degenerate viewport.
    with pytest.raises(ValueError):
        World(maps=[]).maps_in_region(-50, -50, 0, -5)


# ---------------------------------------------------------------------------
# maps_in_region: overlapping maps and ordering preservation
# ---------------------------------------------------------------------------


def test_overlapping_maps_both_returned_in_world_order():
    first = make_world_map(Path("first.json"), 0, 0)
    second = make_world_map(Path("second.json"), 50, 50)
    world_obj = World(maps=[first, second])

    result = world_obj.maps_in_region(25, 25, 50, 50)
    assert result == [first, second]


def test_region_results_preserve_world_file_order():
    maps = [
        make_world_map(Path("c.json"), 200, 200),
        make_world_map(Path("a.json"), 0, 0),
        make_world_map(Path("b.json"), 50, 50),
    ]
    world_obj = World(maps=maps)

    result = world_obj.maps_in_region(0, 0, 300, 300)
    assert result == maps


def test_empty_world_region_query():
    assert World(maps=[]).maps_in_region(0, 0, 100, 100) == []


def test_world_map_bounds_property():
    wm = make_world_map(Path("a.json"), -10, 20, 30, 40)
    assert wm.bounds == (-10, 20, 20, 60)


# ---------------------------------------------------------------------------
# Invalid map dimensions when parsing a world
# ---------------------------------------------------------------------------


def test_invalid_world_map_size_raises_with_context():
    with pytest.raises(ValueError) as exc_info:
        world.parse_world(INVALID_SIZE_WORLD, encoding="utf-8")
    message = str(exc_info.value)
    assert "map_01.json" in message
    assert "greater than zero" in message


def test_world_map_constructor_rejects_zero_and_negative_sizes():
    with pytest.raises(ValueError):
        WorldMap(
            map_file=Path("a.json"),
            size=common_types.Size(0, 100),
            coordinates=common_types.OrderedPair(0, 0),
        )
    with pytest.raises(ValueError):
        WorldMap(
            map_file=Path("a.json"),
            size=common_types.Size(100, -5),
            coordinates=common_types.OrderedPair(0, 0),
        )


# ---------------------------------------------------------------------------
# Integration with the on-disk fixtures: explicit maps + patterns mixed world
# ---------------------------------------------------------------------------


def test_mixed_world_region_hits_and_order():
    parsed_world = world.parse_world(BOTH_WORLD, encoding="utf-8")
    # The "both" fixture: manual map at (-160, 0), pattern maps at
    # (0, 0) and (0, 160), each 160x160.
    assert len(parsed_world.maps) == 3

    # Viewport covering the manual map and part of the first pattern map.
    result = parsed_world.maps_in_region(-160, 0, 200, 160)
    assert [wm.map_file.name for wm in result] == [
        "map_manual_one.json",
        "map_p0-n0.json",
    ]

    # A viewport stretching across all three maps.
    result = parsed_world.maps_in_region(-200, -200, 500, 500)
    assert [wm.map_file.name for wm in result] == [
        "map_manual_one.json",
        "map_p0-n0.json",
        "map_p0-n1.json",
    ]

    # Tangent viewport (starts exactly where the manual map ends) skips it.
    result = parsed_world.maps_in_region(0, 0, 160, 160)
    assert [wm.map_file.name for wm in result] == ["map_p0-n0.json"]


def test_static_world_region_query():
    parsed_world = world.parse_world(STATIC_WORLD, encoding="utf-8")
    result = parsed_world.maps_in_region(150, 0, 100, 160)
    # One pixel of overlap with map_01 (ends at 160) plus all of map_02.
    assert [wm.map_file.name for wm in result] == ["map_01.json", "map_02.json"]


# ---------------------------------------------------------------------------
# On-demand loading: caching, deduplication and opt-out behavior
# ---------------------------------------------------------------------------


def test_load_maps_parses_each_hit_once():
    parsed_world = world.parse_world(BOTH_WORLD, encoding="utf-8")
    loaded = parsed_world.load_maps(-200, -200, 500, 500)

    assert len(loaded) == 3
    assert all(isinstance(m, TiledMap) for m in loaded)
    # Order still follows the world file.
    assert loaded[0] is parsed_world.load_world_map(parsed_world.maps[0])
    assert len(parsed_world._parsed_maps) == 3


def test_load_maps_caches_across_queries():
    parsed_world = world.parse_world(BOTH_WORLD, encoding="utf-8")

    first = parsed_world.load_maps(0, 0, 160, 160)
    assert len(first) == 1
    second = parsed_world.load_maps(0, 0, 160, 160)
    # Same object is returned from the cache rather than re-parsed.
    assert second == first
    assert second[0] is first[0]
    assert len(parsed_world._parsed_maps) == 1


def test_load_world_map_caches_by_resolved_path():
    parsed_world = world.parse_world(BOTH_WORLD, encoding="utf-8")
    target = parsed_world.maps[1]

    # A second WorldMap pointing at the same file via a different path string
    # must resolve to the same cache entry.
    duplicate = WorldMap(
        map_file=Path(os.path.relpath(target.map_file)),
        size=target.size,
        coordinates=target.coordinates,
    )
    loaded_one = parsed_world.load_world_map(target)
    loaded_two = parsed_world.load_world_map(duplicate)
    assert loaded_one is loaded_two
    assert len(parsed_world._parsed_maps) == 1


def test_load_maps_deduplicates_same_file_references():
    shared_file = WORLD_TESTS / "both" / "map_p0-n0.json"
    first = make_world_map(shared_file, 0, 0)
    second = make_world_map(shared_file, 50, 50)
    parsed_world = World(maps=[first, second])

    loaded = parsed_world.load_maps(0, 0, 200, 200)
    assert len(loaded) == 1
    assert len(parsed_world._parsed_maps) == 1


def test_caller_can_opt_out_of_loading():
    # maps_in_region never touches the filesystem, so pointing at a
    # nonexistent file must still succeed for a pure query.
    missing = make_world_map(Path("does_not_exist.json"), 0, 0)
    parsed_world = World(maps=[missing])

    result = parsed_world.maps_in_region(0, 0, 50, 50)
    assert result == [missing]
    assert parsed_world._parsed_maps == {}


def test_load_missing_file_raises_with_context():
    missing = make_world_map(
        (WORLD_TESTS / "both" / "missing_map.json").resolve(), 0, 0
    )
    parsed_world = World(maps=[missing])

    with pytest.raises(FileNotFoundError) as exc_info:
        parsed_world.load_world_map(missing)
    assert "missing_map.json" in str(exc_info.value)
    # A failed load must not populate the cache.
    assert parsed_world._parsed_maps == {}


def test_load_maps_invalid_viewport_raises_without_loading():
    parsed_world = world.parse_world(BOTH_WORLD, encoding="utf-8")
    with pytest.raises(ValueError):
        parsed_world.load_maps(0, 0, 0, 100)
    assert parsed_world._parsed_maps == {}


def test_load_maps_empty_region_returns_empty_list():
    parsed_world = world.parse_world(BOTH_WORLD, encoding="utf-8")
    assert parsed_world.load_maps(5000, 5000, 10, 10) == []
    assert parsed_world._parsed_maps == {}
