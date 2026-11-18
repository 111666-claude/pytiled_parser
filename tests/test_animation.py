"""Tests for animation tile lookups.

Covers the ``pytiled_parser.animation`` module against both the JSON and TMX
parsers, using two hand-authored fixtures with unequal-duration animations:

* ``map_tests/animation`` - a regular (non-infinite) 4x4 map with two tilesets.
* ``map_tests/animation_infinite`` - an infinite map using chunks.

Both fixtures contain flipped cells, static tiles, an empty animation, an
animation whose frames all have non-positive durations, an animation pointing
at an unknown tile ID, an unknown GID and empty cells.
"""

import os
from pathlib import Path

import pytest

from pytiled_parser import parse_map
from pytiled_parser.animation import (
    FLIPPED_DIAGONALLY_FLAG,
    FLIPPED_HORIZONTALLY_FLAG,
    FLIPPED_VERTICALLY_FLAG,
    GID_MASK,
    get_cell_gid,
    get_flip_flags,
    get_tile_gid_at,
    get_tileset_for_gid,
    strip_flip_flags,
)
from pytiled_parser.common_types import OrderedPair
from pytiled_parser.tileset import Frame, Tile, Tileset

TESTS_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
TEST_DATA = TESTS_DIR / "test_data"
MAP_TESTS = TEST_DATA / "map_tests"

ANIMATION_MAP = MAP_TESTS / "animation"
INFINITE_MAP = MAP_TESTS / "animation_infinite"

PARSER_TYPES = ["json", "tmx"]


@pytest.fixture(params=PARSER_TYPES)
def animation_map(request):
    return parse_map(ANIMATION_MAP / f"map.{request.param}")


@pytest.fixture(params=PARSER_TYPES)
def infinite_map(request):
    return parse_map(INFINITE_MAP / f"map.{request.param}")


# The animation on local tile 0 of the first tileset uses unequal durations:
# 100ms -> local 0 (GID 1), 200ms -> local 1 (GID 2), 300ms -> local 2 (GID 3).
TILESET_ONE_BOUNDARIES = [
    (0, 1),
    (99, 1),
    (100, 2),
    (299, 2),
    (300, 3),
    (599, 3),
    (600, 1),  # loop restarts
    (601, 1),
    (1199, 3),
    (1200, 1),
]

# The second tileset animation: 100ms -> local 0 (GID 9), 300ms -> local 1
# (GID 10), total period 400ms.
TILESET_TWO_BOUNDARIES = [
    (0, 9),
    (99, 9),
    (100, 10),
    (399, 10),
    (400, 9),
    (401, 9),
    (799, 10),
    (800, 9),
]


@pytest.mark.parametrize("time_ms,expected", TILESET_ONE_BOUNDARIES)
def test_animation_boundary_times(animation_map, time_ms, expected):
    layer = animation_map.layers[0]
    assert (
        get_tile_gid_at(layer, animation_map.tilesets, OrderedPair(0, 0), time_ms)
        == expected
    )


@pytest.mark.parametrize("time_ms,expected", TILESET_TWO_BOUNDARIES)
def test_second_tileset_animation(animation_map, time_ms, expected):
    layer = animation_map.layers[0]
    assert (
        get_tile_gid_at(layer, animation_map.tilesets, OrderedPair(0, 3), time_ms)
        == expected
    )


def test_static_tile_returned_unchanged(animation_map):
    layer = animation_map.layers[0]
    for time_ms in (0, 1, 500, 99999):
        assert (
            get_tile_gid_at(layer, animation_map.tilesets, OrderedPair(1, 0), time_ms)
            == 2
        )


def test_flip_flags_preserved_animated(animation_map):
    # Cell (2, 1) stores GID 1 with the horizontal flip flag set.
    layer = animation_map.layers[0]
    expected = [
        (0, FLIPPED_HORIZONTALLY_FLAG + 1),
        (100, FLIPPED_HORIZONTALLY_FLAG + 2),
        (300, FLIPPED_HORIZONTALLY_FLAG + 3),
        (600, FLIPPED_HORIZONTALLY_FLAG + 1),
        (1200, FLIPPED_HORIZONTALLY_FLAG + 1),
    ]
    for time_ms, want in expected:
        assert (
            get_tile_gid_at(layer, animation_map.tilesets, OrderedPair(2, 1), time_ms)
            == want
        )


def test_flip_flags_preserved_static(animation_map):
    layer = animation_map.layers[0]
    # Vertical flip on static GID 1.
    assert (
        get_tile_gid_at(layer, animation_map.tilesets, OrderedPair(3, 1), 0)
        == FLIPPED_VERTICALLY_FLAG + 1
    )
    # Diagonal flip on static GID 1.
    assert (
        get_tile_gid_at(layer, animation_map.tilesets, OrderedPair(0, 2), 0)
        == FLIPPED_DIAGONALLY_FLAG + 1
    )


def test_empty_animation_is_static(animation_map):
    # Tile local 4 (GID 5) has an empty animation list.
    layer = animation_map.layers[0]
    for time_ms in (0, 50, 500):
        assert (
            get_tile_gid_at(layer, animation_map.tilesets, OrderedPair(1, 2), time_ms)
            == 5
        )


def test_non_positive_durations_are_static(animation_map):
    # Tile local 5 (GID 6) only has zero and negative duration frames.
    layer = animation_map.layers[0]
    for time_ms in (0, 1, 999):
        assert (
            get_tile_gid_at(layer, animation_map.tilesets, OrderedPair(2, 2), time_ms)
            == 6
        )


def test_unknown_frame_tile_id_returns_none(animation_map):
    # Tile local 6 (GID 7) animates to local tile 99, which doesn't exist.
    layer = animation_map.layers[0]
    for time_ms in (0, 149, 150):
        assert (
            get_tile_gid_at(layer, animation_map.tilesets, OrderedPair(3, 2), time_ms)
            is None
        )


def test_unknown_gid_returns_none(animation_map):
    layer = animation_map.layers[0]
    assert get_tile_gid_at(layer, animation_map.tilesets, OrderedPair(2, 3), 0) is None


def test_empty_cell_returns_zero(animation_map):
    layer = animation_map.layers[0]
    assert get_tile_gid_at(layer, animation_map.tilesets, OrderedPair(1, 1), 0) == 0


@pytest.mark.parametrize("coordinate", [(4, 0), (0, 4), (4, 4), (-1, 0), (0, -1)])
def test_out_of_bounds_returns_none(animation_map, coordinate):
    layer = animation_map.layers[0]
    x, y = coordinate
    assert get_tile_gid_at(layer, animation_map.tilesets, OrderedPair(x, y), 0) is None


def test_negative_time_raises(animation_map):
    layer = animation_map.layers[0]
    with pytest.raises(ValueError):
        get_tile_gid_at(layer, animation_map.tilesets, OrderedPair(0, 0), -1)
    with pytest.raises(ValueError):
        get_tile_gid_at(layer, animation_map.tilesets, OrderedPair(0, 0), -1000000)


@pytest.mark.parametrize("coordinate", [OrderedPair(1.5, 0), OrderedPair(0, 2.0)])
def test_non_integer_coordinates_raise(animation_map, coordinate):
    layer = animation_map.layers[0]
    with pytest.raises(ValueError):
        get_tile_gid_at(layer, animation_map.tilesets, coordinate, 0)


def test_boolean_coordinates_raise(animation_map):
    layer = animation_map.layers[0]
    with pytest.raises(ValueError):
        get_tile_gid_at(layer, animation_map.tilesets, OrderedPair(True, 0), 0)


def test_cycle_overflow_wraps(animation_map):
    layer = animation_map.layers[0]
    # Huge elapsed times must wrap around the 600ms / 400ms periods.
    assert (
        get_tile_gid_at(
            layer, animation_map.tilesets, OrderedPair(0, 0), 600 * 1000003 + 100
        )
        == 2
    )
    assert (
        get_tile_gid_at(
            layer, animation_map.tilesets, OrderedPair(0, 3), 400 * 777777 + 100
        )
        == 10
    )


def test_infinite_map_chunks(infinite_map):
    layer = infinite_map.layers[0]
    assert layer.chunks is not None
    assert len(layer.chunks) == 2

    # First chunk: same unequal-duration animation as the finite fixture.
    for time_ms, expected in TILESET_ONE_BOUNDARIES:
        assert (
            get_tile_gid_at(layer, infinite_map.tilesets, OrderedPair(0, 0), time_ms)
            == expected
        )

    # Flipped animated cell inside the first chunk.
    assert (
        get_tile_gid_at(layer, infinite_map.tilesets, OrderedPair(2, 1), 0)
        == FLIPPED_HORIZONTALLY_FLAG + 1
    )
    assert (
        get_tile_gid_at(layer, infinite_map.tilesets, OrderedPair(2, 1), 100)
        == FLIPPED_HORIZONTALLY_FLAG + 2
    )


def test_infinite_map_second_chunk(infinite_map):
    layer = infinite_map.layers[0]
    # Second chunk starts at x=4.
    for time_ms, expected in TILESET_TWO_BOUNDARIES:
        assert (
            get_tile_gid_at(layer, infinite_map.tilesets, OrderedPair(4, 0), time_ms)
            == expected
        )
    # Flipped animated cell from the second tileset (GID 9 + hflip).
    assert (
        get_tile_gid_at(layer, infinite_map.tilesets, OrderedPair(7, 0), 0)
        == FLIPPED_HORIZONTALLY_FLAG + 9
    )
    assert (
        get_tile_gid_at(layer, infinite_map.tilesets, OrderedPair(7, 0), 100)
        == FLIPPED_HORIZONTALLY_FLAG + 10
    )
    assert (
        get_tile_gid_at(layer, infinite_map.tilesets, OrderedPair(7, 0), 400)
        == FLIPPED_HORIZONTALLY_FLAG + 9
    )


def test_infinite_map_gaps_and_bounds(infinite_map):
    layer = infinite_map.layers[0]
    # Empty cell inside a chunk region.
    assert get_tile_gid_at(layer, infinite_map.tilesets, OrderedPair(3, 3), 0) == 0
    # Past every chunk.
    assert get_tile_gid_at(layer, infinite_map.tilesets, OrderedPair(8, 0), 0) is None
    # Below all chunks.
    assert get_tile_gid_at(layer, infinite_map.tilesets, OrderedPair(0, 4), 0) is None
    # Negative coordinates.
    assert get_tile_gid_at(layer, infinite_map.tilesets, OrderedPair(-1, 0), 0) is None


def test_infinite_map_unknown_frame(infinite_map):
    layer = infinite_map.layers[0]
    assert get_tile_gid_at(layer, infinite_map.tilesets, OrderedPair(3, 2), 0) is None


def test_strip_flip_flags():
    assert strip_flip_flags(0) == 0
    assert strip_flip_flags(42) == 42
    assert strip_flip_flags(FLIPPED_HORIZONTALLY_FLAG + 7) == 7
    assert strip_flip_flags(GID_MASK) == GID_MASK
    assert strip_flip_flags(0xFFFFFFFF) == GID_MASK


def test_get_flip_flags():
    assert get_flip_flags(7) == (False, False, False)
    assert get_flip_flags(FLIPPED_HORIZONTALLY_FLAG + 7) == (True, False, False)
    assert get_flip_flags(FLIPPED_VERTICALLY_FLAG + 7) == (False, True, False)
    assert get_flip_flags(FLIPPED_DIAGONALLY_FLAG + 7) == (False, False, True)
    assert get_flip_flags(0xFFFFFFFF) == (True, True, True)


def test_get_cell_gid_regular(animation_map):
    layer = animation_map.layers[0]
    assert get_cell_gid(layer, 0, 0) == 1
    assert get_cell_gid(layer, 1, 1) == 0
    assert get_cell_gid(layer, 4, 0) is None
    assert get_cell_gid(layer, -1, 0) is None


def test_get_cell_gid_chunks(infinite_map):
    layer = infinite_map.layers[0]
    assert get_cell_gid(layer, 0, 0) == 1
    assert get_cell_gid(layer, 4, 0) == 9
    assert get_cell_gid(layer, 7, 0) == FLIPPED_HORIZONTALLY_FLAG + 9
    assert get_cell_gid(layer, 8, 0) is None


def test_get_tileset_for_gid(animation_map):
    tilesets = animation_map.tilesets
    first = get_tileset_for_gid(tilesets, 1)
    second = get_tileset_for_gid(tilesets, 9)
    assert first is not None and first.name == "animated_tileset"
    assert second is not None and second.name == "second_tileset"
    assert get_tileset_for_gid(tilesets, 8).name == "animated_tileset"
    # GIDs beyond any tileset's range resolve to None.
    assert get_tileset_for_gid(tilesets, 100) is None


def _make_tileset(frames, image=None):
    tileset = Tileset(
        name="synthetic",
        tile_width=32,
        tile_height=32,
        tile_count=3,
        columns=3,
        firstgid=1,
        image=image,
        tiles={
            0: Tile(id=0, animation=frames),
            1: Tile(id=1),
            2: Tile(id=2),
        },
    )
    return tileset


def test_mixed_valid_and_invalid_duration_frames():
    # A valid frame followed by a zero-duration frame: only the valid one counts.
    tileset = _make_tileset(
        [Frame(tile_id=0, duration=100), Frame(tile_id=1, duration=0)]
    )
    layer_data = [[1]]
    from pytiled_parser.layer import TileLayer

    layer = TileLayer(name="l", data=layer_data)
    assert get_tile_gid_at(layer, {1: tileset}, OrderedPair(0, 0), 0) == 1
    assert get_tile_gid_at(layer, {1: tileset}, OrderedPair(0, 0), 99) == 1
    assert get_tile_gid_at(layer, {1: tileset}, OrderedPair(0, 0), 100) == 1


def test_animation_skips_invalid_frame_at_boundary():
    # The invalid frame sits between two valid frames and must never be shown.
    tileset = _make_tileset(
        [
            Frame(tile_id=0, duration=100),
            Frame(tile_id=2, duration=-10),
            Frame(tile_id=1, duration=100),
        ]
    )
    from pytiled_parser.layer import TileLayer

    layer = TileLayer(name="l", data=[[1]])
    assert get_tile_gid_at(layer, {1: tileset}, OrderedPair(0, 0), 0) == 1
    assert get_tile_gid_at(layer, {1: tileset}, OrderedPair(0, 0), 99) == 1
    assert get_tile_gid_at(layer, {1: tileset}, OrderedPair(0, 0), 100) == 2
    assert get_tile_gid_at(layer, {1: tileset}, OrderedPair(0, 0), 199) == 2
    assert get_tile_gid_at(layer, {1: tileset}, OrderedPair(0, 0), 200) == 1


def test_get_tileset_for_gid_below_first_firstgid():
    # GID 0 never belongs to a tileset; nothing covers it.
    from pytiled_parser.tileset import Tileset as _Tileset

    tilesets = {
        1: _Tileset(
            name="only",
            tile_width=32,
            tile_height=32,
            tile_count=2,
            columns=2,
            firstgid=1,
        )
    }
    assert get_tileset_for_gid(tilesets, 0) is None


def test_get_cell_gid_without_data_returns_none():
    # A TileLayer with neither data nor chunks cannot resolve a cell.
    from pytiled_parser.layer import TileLayer

    layer = TileLayer(name="empty")
    assert get_cell_gid(layer, 0, 0) is None


def test_spritesheet_frame_outside_tile_count_is_none():
    # A spritesheet tileset (has an image) implicitly contains IDs 0..count-1,
    # so a frame beyond that range still cannot be resolved.
    from pathlib import Path

    from pytiled_parser.layer import TileLayer

    tileset = _make_tileset([Frame(tile_id=99, duration=100)], image=Path("sheet.png"))
    layer = TileLayer(name="l", data=[[1]])
    assert get_tile_gid_at(layer, {1: tileset}, OrderedPair(0, 0), 0) is None


def test_spritesheet_implicit_tile_ids_resolve():
    # The same spritesheet tileset can resolve a frame whose ID is within
    # tile_count even though it isn't enumerated in the tiles dict.
    from pathlib import Path

    from pytiled_parser.layer import TileLayer

    tileset = _make_tileset([Frame(tile_id=2, duration=100)], image=Path("sheet.png"))
    # Drop tile 2 from the explicit dict to mimic a pure spritesheet.
    del tileset.tiles[2]
    layer = TileLayer(name="l", data=[[1]])
    assert get_tile_gid_at(layer, {1: tileset}, OrderedPair(0, 0), 0) == 3


def test_image_collection_missing_tile_id_is_none():
    # Image collection tileset (no spritesheet): an in-range ID that simply
    # isn't one of the explicitly defined tiles cannot be resolved.
    from pytiled_parser.layer import TileLayer

    tileset = _make_tileset([Frame(tile_id=2, duration=100)])
    # tile_count still says 3, but the image collection only defines 0 and 1.
    del tileset.tiles[2]
    assert tileset.image is None
    layer = TileLayer(name="l", data=[[1]])
    assert get_tile_gid_at(layer, {1: tileset}, OrderedPair(0, 0), 0) is None
