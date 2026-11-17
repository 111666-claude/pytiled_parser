"""Tests for tile collision extraction (pytiled_parser.tile_collision)"""

import os
from pathlib import Path

import pytest

from pytiled_parser import (
    CollisionEllipse,
    CollisionPolygon,
    CollisionRectangle,
    InvalidTileGidError,
    UnsupportedOrientationError,
    extract_tile_collisions,
    parse_map,
)
from pytiled_parser.common_types import OrderedPair

TESTS_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
TEST_DATA = TESTS_DIR / "test_data"
COLLISION_TESTS = TEST_DATA / "tile_collision_tests"

# GID flip flags
H = 0x80000000
V = 0x40000000
D = 0x20000000


def collisions_by_tile(collisions):
    """Index collisions by (layer name, tile coordinates) for easy lookup."""
    return {(c.layer.name, c.tile_coordinates): c for c in collisions}


def test_finite_map_shape_extraction():
    tiled_map = parse_map(COLLISION_TESTS / "map.json")
    collisions = extract_tile_collisions(tiled_map)
    by_tile = collisions_by_tile(collisions)

    # Plain rectangle from tileset_a tile 0 at (0, 0)
    shape = by_tile[("ground", OrderedPair(0, 0))].shape
    assert shape == CollisionRectangle(4, 4, 16, 8)

    # Ellipse from tileset_a tile 1 at (2, 0)
    shape = by_tile[("ground", OrderedPair(2, 0))].shape
    assert shape == CollisionEllipse(64, 0, 16, 32)

    # Polygon from tileset_a tile 2 at (3, 0)
    shape = by_tile[("ground", OrderedPair(3, 0))].shape
    assert shape == CollisionPolygon(
        [OrderedPair(96, 0), OrderedPair(128, 0), OrderedPair(128, 32)]
    )

    # Polygon from the second tileset (firstgid 5) at (0, 1)
    shape = by_tile[("ground", OrderedPair(0, 1))].shape
    assert shape == CollisionPolygon(
        [OrderedPair(8, 40), OrderedPair(24, 40), OrderedPair(8, 56)]
    )


def test_empty_gid_and_tiles_without_collision_are_skipped():
    tiled_map = parse_map(COLLISION_TESTS / "map.json")
    collisions = extract_tile_collisions(tiled_map)
    by_tile = collisions_by_tile(collisions)

    # GID 0 cells produce nothing
    assert ("ground", OrderedPair(3, 2)) not in by_tile
    assert ("ground", OrderedPair(0, 3)) not in by_tile
    # GID 4 maps to tileset_a tile 3, which has no collision objects
    assert ("ground", OrderedPair(1, 0)) not in by_tile

    # 11 shapes total: 10 from "ground" + 1 from "inner", see map.json
    assert len(collisions) == 11


def test_layer_and_group_offsets_are_accumulated():
    tiled_map = parse_map(COLLISION_TESTS / "map.json")
    collisions = extract_tile_collisions(tiled_map)
    by_tile = collisions_by_tile(collisions)

    # "inner" layer has offset (5, 5) inside a group with offset (10, 20)
    collision = by_tile[("inner", OrderedPair(0, 0))]
    assert collision.layer.name == "inner"
    assert collision.shape == CollisionRectangle(4 + 15, 4 + 25, 16, 8)


def test_gid_flip_flags():
    tiled_map = parse_map(COLLISION_TESTS / "map.json")
    collisions = extract_tile_collisions(tiled_map)
    by_tile = collisions_by_tile(collisions)

    # Horizontal flip of the (4, 4, 16, 8) rect within a 32x32 tile at (1, 1)
    collision = by_tile[("ground", OrderedPair(1, 1))]
    assert collision.gid == H | 1
    assert collision.tile_id == 0
    assert collision.shape == CollisionRectangle(32 + 12, 32 + 4, 16, 8)

    # Vertical flip at (2, 1)
    collision = by_tile[("ground", OrderedPair(2, 1))]
    assert collision.gid == V | 1
    assert collision.shape == CollisionRectangle(64 + 4, 32 + 20, 16, 8)

    # Diagonal flip at (3, 1): axes are swapped
    collision = by_tile[("ground", OrderedPair(3, 1))]
    assert collision.gid == D | 1
    assert collision.shape == CollisionRectangle(96 + 4, 32 + 4, 8, 16)

    # Horizontal + vertical flip of the polygon at (0, 2)
    collision = by_tile[("ground", OrderedPair(0, 2))]
    assert collision.gid == H | V | 3
    assert collision.shape == CollisionPolygon(
        [OrderedPair(32, 96), OrderedPair(0, 96), OrderedPair(0, 64)]
    )

    # Diagonal + horizontal + vertical flip of the polygon at (1, 2)
    collision = by_tile[("ground", OrderedPair(1, 2))]
    assert collision.gid == D | H | V | 3
    assert collision.shape == CollisionPolygon(
        [OrderedPair(64, 96), OrderedPair(64, 64), OrderedPair(32, 64)]
    )

    # Diagonal flip of the ellipse at (2, 2): bounding box axes swap
    collision = by_tile[("ground", OrderedPair(2, 2))]
    assert collision.gid == D | 2
    assert collision.shape == CollisionEllipse(64, 64, 32, 16)


def test_infinite_map_negative_chunks():
    tiled_map = parse_map(COLLISION_TESTS / "infinite_map.json")
    collisions = extract_tile_collisions(tiled_map)
    by_tile = collisions_by_tile(collisions)

    # Chunk at (-4, -4): plain rect at tile (-4, -4) -> world origin (-128, -128)
    collision = by_tile[("inf", OrderedPair(-4, -4))]
    assert collision.shape == CollisionRectangle(-128 + 4, -128 + 4, 16, 8)

    # Horizontally flipped rect at tile (-2, -1) -> world origin (-64, -32)
    collision = by_tile[("inf", OrderedPair(-2, -1))]
    assert collision.gid == H | 1
    assert collision.shape == CollisionRectangle(-64 + 12, -32 + 4, 16, 8)

    # Chunk at (0, 0): polygon at tile (1, 1) -> world origin (32, 32)
    collision = by_tile[("inf", OrderedPair(1, 1))]
    assert collision.shape == CollisionPolygon(
        [OrderedPair(32, 32), OrderedPair(64, 32), OrderedPair(64, 64)]
    )

    assert len(collisions) == 3


def test_collision_metadata():
    tiled_map = parse_map(COLLISION_TESTS / "map.json")
    collisions = extract_tile_collisions(tiled_map)
    by_tile = collisions_by_tile(collisions)

    first = by_tile[("ground", OrderedPair(0, 0))]
    assert first.tileset.name == "tileset_a"
    assert first.tile_id == 0
    assert first.gid == 1

    second_tileset = by_tile[("ground", OrderedPair(0, 1))]
    assert second_tileset.tileset.name == "tileset_b"
    assert second_tileset.tile_id == 0
    assert second_tileset.gid == 5


def test_non_orthogonal_map_raises():
    tiled_map = parse_map(TEST_DATA / "map_tests" / "hexagonal" / "map.json")
    with pytest.raises(UnsupportedOrientationError):
        extract_tile_collisions(tiled_map)


def test_unknown_gid_raises():
    tiled_map = parse_map(COLLISION_TESTS / "unknown_gid_map.json")
    with pytest.raises(InvalidTileGidError):
        extract_tile_collisions(tiled_map)


def test_gid_out_of_tileset_range_raises():
    # GID 6 belongs to tileset_b (firstgid 5) but exceeds its tile_count of 1
    tiled_map = parse_map(COLLISION_TESTS / "gid_out_of_range_map.json")
    with pytest.raises(InvalidTileGidError):
        extract_tile_collisions(tiled_map)


def test_hexagonal_rotation_flag_raises():
    tiled_map = parse_map(COLLISION_TESTS / "hex_flag_map.json")
    with pytest.raises(InvalidTileGidError):
        extract_tile_collisions(tiled_map)
