"""Tests for tile/pixel coordinate conversion (pytiled_parser.coordinates)."""

import os
from pathlib import Path

import pytest

from pytiled_parser import OrderedPair, Size, TiledMap, parse_map
from pytiled_parser.coordinates import pixel_to_tile, tile_to_pixel

TESTS_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
TEST_DATA = TESTS_DIR / "test_data"
MAP_TESTS = TEST_DATA / "map_tests"


def make_map(
    orientation,
    tile_size=(32, 32),
    map_size=(10, 10),
    hex_side_length=None,
    stagger_axis=None,
    stagger_index=None,
):
    return TiledMap(
        map_file=Path("map.json"),
        infinite=False,
        layers=[],
        map_size=Size(*map_size),
        next_layer_id=None,
        next_object_id=1,
        orientation=orientation,
        render_order="right-down",
        tiled_version="1.9.0",
        tile_size=Size(*tile_size),
        tilesets={},
        version="1.9",
        hex_side_length=hex_side_length,
        stagger_axis=stagger_axis,
        stagger_index=stagger_index,
    )


def tile_center(tiled_map, tile_x, tile_y):
    """Pixel position of the visual center of a tile."""
    anchor = tile_to_pixel(tiled_map, tile_x, tile_y)
    if tiled_map.orientation == "isometric":
        # the anchor is the top corner of the diamond
        return (anchor.x, anchor.y + tiled_map.tile_size.height / 2)
    # the anchor is the top-left corner of the tile's bounding box
    return (
        anchor.x + tiled_map.tile_size.width / 2,
        anchor.y + tiled_map.tile_size.height / 2,
    )


def assert_center_round_trip(tiled_map, tile_range=range(-4, 5)):
    """tile -> anchor -> center -> tile must be the identity."""
    for tile_x in tile_range:
        for tile_y in tile_range:
            center = tile_center(tiled_map, tile_x, tile_y)
            hit = pixel_to_tile(tiled_map, *center)
            assert (hit.x, hit.y) == (tile_x, tile_y), (
                f"center round trip failed for tile ({tile_x}, {tile_y}): "
                f"center={center}, hit={hit}"
            )


# --- Orthogonal -------------------------------------------------------------

ORTHOGONAL_MAP = make_map("orthogonal", tile_size=(32, 32))


def test_orthogonal_tile_to_pixel():
    assert tile_to_pixel(ORTHOGONAL_MAP, 0, 0) == (0, 0)
    assert tile_to_pixel(ORTHOGONAL_MAP, 2, 3) == (64, 96)
    assert tile_to_pixel(ORTHOGONAL_MAP, -1, -2) == (-32, -64)


def test_orthogonal_pixel_to_tile():
    assert pixel_to_tile(ORTHOGONAL_MAP, 0, 0) == (0, 0)
    assert pixel_to_tile(ORTHOGONAL_MAP, 63.9, 95.9) == (1, 2)
    # exact boundaries resolve to the tile below/right
    assert pixel_to_tile(ORTHOGONAL_MAP, 64, 96) == (2, 3)
    assert pixel_to_tile(ORTHOGONAL_MAP, 63.999, 96) == (1, 3)
    # negative pixels floor away from zero
    assert pixel_to_tile(ORTHOGONAL_MAP, -0.1, -0.1) == (-1, -1)
    assert pixel_to_tile(ORTHOGONAL_MAP, -32, -32) == (-1, -1)


def test_orthogonal_center_round_trip():
    assert_center_round_trip(ORTHOGONAL_MAP)


# --- Isometric --------------------------------------------------------------

# 10x10 map of 64x32 tiles
ISOMETRIC_MAP = make_map("isometric", tile_size=(64, 32), map_size=(10, 10))


def test_isometric_tile_to_pixel():
    # origin x is map_height * tile_width / 2
    assert tile_to_pixel(ISOMETRIC_MAP, 0, 0) == (320, 0)
    assert tile_to_pixel(ISOMETRIC_MAP, 1, 0) == (352, 16)
    assert tile_to_pixel(ISOMETRIC_MAP, 0, 1) == (288, 16)
    assert tile_to_pixel(ISOMETRIC_MAP, 2, 3) == (288, 80)


def test_isometric_pixel_to_tile():
    # anchor points hit their own tile
    assert pixel_to_tile(ISOMETRIC_MAP, 320, 0) == (0, 0)
    assert pixel_to_tile(ISOMETRIC_MAP, 288, 80) == (2, 3)
    # center of tile (2, 3): the anchor is the top corner of the diamond
    assert pixel_to_tile(ISOMETRIC_MAP, 288, 80 + 16) == (2, 3)
    # just outside the left corner of tile (0, 0)'s diamond
    assert pixel_to_tile(ISOMETRIC_MAP, 319.9, 15.9) == (0, 0)


def test_isometric_center_round_trip():
    assert_center_round_trip(ISOMETRIC_MAP)


# --- Staggered --------------------------------------------------------------

STAGGERED_Y_ODD = make_map(
    "staggered", tile_size=(32, 16), stagger_axis="y", stagger_index="odd"
)
STAGGERED_X_EVEN = make_map(
    "staggered", tile_size=(32, 16), stagger_axis="x", stagger_index="even"
)


def test_staggered_y_odd_tile_to_pixel():
    # rows are half a tile-height apart; odd rows shift right half a tile
    assert tile_to_pixel(STAGGERED_Y_ODD, 0, 0) == (0, 0)
    assert tile_to_pixel(STAGGERED_Y_ODD, 1, 0) == (32, 0)
    assert tile_to_pixel(STAGGERED_Y_ODD, 0, 1) == (16, 8)
    assert tile_to_pixel(STAGGERED_Y_ODD, 2, 1) == (80, 8)
    assert tile_to_pixel(STAGGERED_Y_ODD, 0, 2) == (0, 16)


def test_staggered_x_even_tile_to_pixel():
    # columns are half a tile-width apart; even columns shift down
    assert tile_to_pixel(STAGGERED_X_EVEN, 0, 0) == (0, 8)
    assert tile_to_pixel(STAGGERED_X_EVEN, 1, 0) == (16, 0)
    assert tile_to_pixel(STAGGERED_X_EVEN, 2, 1) == (32, 24)


def test_staggered_pixel_to_tile_hits():
    # centers of the tiles checked above
    assert pixel_to_tile(STAGGERED_Y_ODD, 16, 8) == (0, 0)
    assert pixel_to_tile(STAGGERED_Y_ODD, 32, 16) == (0, 1)
    assert pixel_to_tile(STAGGERED_X_EVEN, 16, 16) == (0, 0)
    assert pixel_to_tile(STAGGERED_X_EVEN, 32, 8) == (1, 0)


@pytest.mark.parametrize("axis", ["x", "y"])
@pytest.mark.parametrize("index", ["odd", "even"])
def test_staggered_center_round_trip(axis, index):
    tiled_map = make_map(
        "staggered", tile_size=(32, 16), stagger_axis=axis, stagger_index=index
    )
    assert_center_round_trip(tiled_map)


# --- Hexagonal --------------------------------------------------------------

# Matches tests/test_data/map_tests/hexagonal: 14x12 tiles, side length 6
HEXAGONAL_Y_ODD = make_map(
    "hexagonal",
    tile_size=(14, 12),
    hex_side_length=6,
    stagger_axis="y",
    stagger_index="odd",
)
HEXAGONAL_X_ODD = make_map(
    "hexagonal",
    tile_size=(14, 12),
    hex_side_length=6,
    stagger_axis="x",
    stagger_index="odd",
)


def test_hexagonal_y_odd_tile_to_pixel():
    # rows are row_height (9) apart; odd rows shift right by column_width (7)
    assert tile_to_pixel(HEXAGONAL_Y_ODD, 0, 0) == (0, 0)
    assert tile_to_pixel(HEXAGONAL_Y_ODD, 1, 0) == (14, 0)
    assert tile_to_pixel(HEXAGONAL_Y_ODD, 0, 1) == (7, 9)
    assert tile_to_pixel(HEXAGONAL_Y_ODD, 0, 2) == (0, 18)


def test_hexagonal_x_odd_tile_to_pixel():
    # columns are column_width (10) apart; odd columns shift down by 6
    assert tile_to_pixel(HEXAGONAL_X_ODD, 0, 0) == (0, 0)
    assert tile_to_pixel(HEXAGONAL_X_ODD, 1, 0) == (10, 6)
    assert tile_to_pixel(HEXAGONAL_X_ODD, 0, 1) == (0, 12)
    assert tile_to_pixel(HEXAGONAL_X_ODD, 2, 3) == (20, 36)


def test_hexagonal_pixel_to_tile_hits():
    # centers of the tiles checked above
    assert pixel_to_tile(HEXAGONAL_Y_ODD, 7, 6) == (0, 0)
    assert pixel_to_tile(HEXAGONAL_Y_ODD, 14, 15) == (0, 1)
    assert pixel_to_tile(HEXAGONAL_X_ODD, 7, 6) == (0, 0)
    assert pixel_to_tile(HEXAGONAL_X_ODD, 17, 12) == (1, 0)
    # the bounding-box corner of a hexagon belongs to a neighbor:
    # (0, 0) is inside the hexagon of tile (-1, -1)
    assert pixel_to_tile(HEXAGONAL_Y_ODD, 0, 0) == (-1, -1)


@pytest.mark.parametrize("axis", ["x", "y"])
@pytest.mark.parametrize("index", ["odd", "even"])
def test_hexagonal_center_round_trip(axis, index):
    tiled_map = make_map(
        "hexagonal",
        tile_size=(14, 12),
        hex_side_length=6,
        stagger_axis=axis,
        stagger_index=index,
    )
    assert_center_round_trip(tiled_map)


def test_hexagonal_parsed_map_round_trip():
    """Conversions work on a map parsed from disk (JSON and TMX)."""
    for map_file in ("map.json", "map.tmx"):
        tiled_map = parse_map(MAP_TESTS / "hexagonal" / map_file)
        assert tiled_map.orientation == "hexagonal"
        assert tile_to_pixel(tiled_map, 0, 1) == (7, 9)
        assert pixel_to_tile(tiled_map, 14, 15) == (0, 1)
        half_w = tiled_map.tile_size.width / 2
        half_h = tiled_map.tile_size.height / 2
        for tile_x in range(10):
            for tile_y in range(10):
                anchor = tile_to_pixel(tiled_map, tile_x, tile_y)
                hit = pixel_to_tile(tiled_map, anchor.x + half_w, anchor.y + half_h)
                assert (hit.x, hit.y) == (tile_x, tile_y)


# --- Deterministic edge cases -----------------------------------------------


def test_non_positive_tile_size_raises():
    bad_map = make_map("orthogonal", tile_size=(0, 32))
    with pytest.raises(ValueError):
        tile_to_pixel(bad_map, 0, 0)
    with pytest.raises(ValueError):
        pixel_to_tile(bad_map, 0, 0)

    negative_map = make_map("hexagonal", tile_size=(14, -12), hex_side_length=6)
    with pytest.raises(ValueError):
        tile_to_pixel(negative_map, 0, 0)
    with pytest.raises(ValueError):
        pixel_to_tile(negative_map, 0, 0)


def test_unknown_orientation_raises():
    bad_map = make_map("diagonal")
    with pytest.raises(ValueError):
        tile_to_pixel(bad_map, 0, 0)
    with pytest.raises(ValueError):
        pixel_to_tile(bad_map, 0, 0)


def test_results_are_ordered_pairs():
    anchor = tile_to_pixel(ORTHOGONAL_MAP, 1, 1)
    hit = pixel_to_tile(ORTHOGONAL_MAP, 5, 5)
    assert isinstance(anchor, OrderedPair)
    assert isinstance(hit, OrderedPair)
    assert isinstance(hit.x, int) and isinstance(hit.y, int)
