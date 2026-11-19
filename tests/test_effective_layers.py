"""Tests for effective layer queries (recursive group composition)."""

import os
from pathlib import Path

import pytest

from pytiled_parser import (
    Color,
    CyclicLayerGroupError,
    InvalidTintColorError,
    LayerGroup,
    OrderedPair,
    Size,
    TileLayer,
    UnsupportedOrientationError,
    decode_gid,
    get_effective_tile_layers,
    iter_effective_tile_layers,
    parse_map,
)
from pytiled_parser.effective_layers import GID_MASK
from pytiled_parser.layer import Chunk

TESTS_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
TEST_DATA = TESTS_DIR / "test_data"
MAP_TESTS = TEST_DATA / "map_tests"

NESTED_GROUPS = MAP_TESTS / "nested_groups"
NESTED_GROUPS_INFINITE = MAP_TESTS / "nested_groups_infinite"


def _map_path(fixture_dir: Path, parser_type: str) -> Path:
    return fixture_dir / ("map.json" if parser_type == "json" else "map.tmx")


@pytest.mark.parametrize("parser_type", ["json", "tmx"])
def test_draw_order_and_hidden_ancestors(parser_type):
    tiled_map = parse_map(_map_path(NESTED_GROUPS, parser_type))
    result = get_effective_tile_layers(tiled_map, OrderedPair(36, 40))

    # Back to front draw order; hidden group/layer and empty/object/image
    # layers never appear.
    assert [entry.name for entry in result] == [
        "Background",
        "Collision",
        "Lighting",
        "Flipped",
    ]


@pytest.mark.parametrize("parser_type", ["json", "tmx"])
def test_effective_opacity(parser_type):
    tiled_map = parse_map(_map_path(NESTED_GROUPS, parser_type))
    result = {
        entry.name: entry
        for entry in get_effective_tile_layers(tiled_map, OrderedPair(36, 40))
    }

    assert result["Background"].opacity == 1.0
    # Outer group (0.8) * Lighting (1.0)
    assert result["Lighting"].opacity == pytest.approx(0.8)
    # Outer group (0.8) * Inner group (0.5) * Collision (0.5)
    assert result["Collision"].opacity == pytest.approx(0.2)


@pytest.mark.parametrize("parser_type", ["json", "tmx"])
def test_effective_offset_is_recursive_sum(parser_type):
    tiled_map = parse_map(_map_path(NESTED_GROUPS, parser_type))
    result = {
        entry.name: entry
        for entry in get_effective_tile_layers(tiled_map, OrderedPair(36, 40))
    }

    assert result["Background"].offset == OrderedPair(0, 0)
    # Outer group (10, 20) + Lighting (0, 0)
    assert result["Lighting"].offset == OrderedPair(10, 20)
    # Outer group (10, 20) + Inner group (4, 8) + Collision (0, 0)
    assert result["Collision"].offset == OrderedPair(14, 28)


@pytest.mark.parametrize("parser_type", ["json", "tmx"])
def test_effective_tint_is_channel_wise_product(parser_type):
    tiled_map = parse_map(_map_path(NESTED_GROUPS, parser_type))
    result = {
        entry.name: entry
        for entry in get_effective_tile_layers(tiled_map, OrderedPair(36, 40))
    }

    # No tint in the chain.
    assert result["Background"].tint_color == Color(255, 255, 0, 255)
    assert result["Flipped"].tint_color is None

    # Tiled colors are #AARRGGBB, so the outer group's #80ff00ff is
    # RGBA(255, 0, 255, 128).
    # Outer group * Lighting's #00ff00 (RGBA(0,255,0,255)):
    # each channel is the product divided by 255 -> RGBA(0, 0, 0, 128)
    assert result["Lighting"].tint_color == Color(0, 0, 0, 128)

    # Outer group * Inner group #0000ff (RGBA(0,0,255,255)); Collision has
    # no tint of its own -> RGBA(0, 0, 255, 128)
    assert result["Collision"].tint_color == Color(0, 0, 255, 128)


@pytest.mark.parametrize("parser_type", ["json", "tmx"])
def test_tile_coordinates_account_for_effective_offset(parser_type):
    tiled_map = parse_map(_map_path(NESTED_GROUPS, parser_type))
    result = {
        entry.name: entry
        for entry in get_effective_tile_layers(tiled_map, OrderedPair(36, 40))
    }

    # No offset: (36, 40) on a 32px grid lands in cell (1, 1).
    assert result["Background"].tile_coordinates == OrderedPair(1, 1)

    # Lighting effective offset (10, 20): local point is (30, 20) -> cell (0, 0).
    assert result["Lighting"].tile_coordinates == OrderedPair(0, 0)

    # Collision effective offset (14, 28): local point is (22, 12) -> cell (0, 0).
    assert result["Collision"].tile_coordinates == OrderedPair(0, 0)


@pytest.mark.parametrize("parser_type", ["json", "tmx"])
def test_flipped_gid_decoding(parser_type):
    tiled_map = parse_map(_map_path(NESTED_GROUPS, parser_type))
    result = {
        entry.name: entry
        for entry in get_effective_tile_layers(tiled_map, OrderedPair(36, 40))
    }

    flipped = result["Flipped"]
    # 0xA0000002 (2684354562) carries horizontal + diagonal flags over GID 2.
    assert flipped.gid == 0x80000000 | 0x20000000 | 2
    assert flipped.gid_without_flags == 2
    assert flipped.flipped_horizontally is True
    assert flipped.flipped_vertically is False
    assert flipped.flipped_diagonally is True


def test_decode_gid_all_flags():
    assert decode_gid(0) == (0, False, False, False)
    assert decode_gid(5) == (5, False, False, False)
    assert decode_gid(0x80000000 | 0x40000000 | 0x20000000 | 7) == (
        7,
        True,
        True,
        True,
    )
    # Flag bits never leak into the stripped GID.
    assert decode_gid(0xFFFFFFFF).gid == GID_MASK
    assert GID_MASK == 0x1FFFFFFF


@pytest.mark.parametrize("parser_type", ["json", "tmx"])
def test_fractional_offset_uses_floor(parser_type):
    tiled_map = parse_map(_map_path(NESTED_GROUPS, parser_type))

    # The Fractional Offset layer shifts 15.5px right. Its GID 6 tile is at
    # cell (0, 2), so it covers pixels x in [15.5, 47.5) on row 2.
    inside = get_effective_tile_layers(tiled_map, OrderedPair(16, 70))
    assert any(entry.name == "Fractional Offset" for entry in inside)

    # One pixel further left floors to cell (-1, 2), which is empty.
    outside = get_effective_tile_layers(tiled_map, OrderedPair(15, 70))
    assert not any(entry.name == "Fractional Offset" for entry in outside)


@pytest.mark.parametrize("parser_type", ["json", "tmx"])
def test_negative_query_coordinates(parser_type):
    tiled_map = parse_map(_map_path(NESTED_GROUPS, parser_type))

    # No layer has cells in negative space; this must not index backwards.
    assert get_effective_tile_layers(tiled_map, OrderedPair(-1, -1)) == []
    assert get_effective_tile_layers(tiled_map, OrderedPair(-100, -100)) == []


@pytest.mark.parametrize("parser_type", ["json", "tmx"])
def test_negative_layer_offset(parser_type):
    tiled_map = parse_map(_map_path(NESTED_GROUPS, parser_type))

    # Negative Offset moves its GID 7 tile (cell (0, 2)) to pixel (-64, 32).
    hit = get_effective_tile_layers(tiled_map, OrderedPair(-64, 32))
    names = [entry.name for entry in hit]
    assert "Negative Offset" in names
    negative_entry = next(entry for entry in hit if entry.name == "Negative Offset")
    assert negative_entry.offset == OrderedPair(-64, -32)
    assert negative_entry.tile_coordinates == OrderedPair(0, 2)
    assert negative_entry.gid == 7


@pytest.mark.parametrize("parser_type", ["json", "tmx"])
def test_empty_layers_are_skipped(parser_type):
    tiled_map = parse_map(_map_path(NESTED_GROUPS, parser_type))
    result = get_effective_tile_layers(tiled_map, OrderedPair(36, 40))
    names = [entry.name for entry in result]
    assert "Empty Top" not in names
    assert "Empty Grandchild" not in names

    # Every point returns an empty list when nothing covers it.
    assert get_effective_tile_layers(tiled_map, OrderedPair(200, 200)) == []


@pytest.mark.parametrize("parser_type", ["json", "tmx"])
def test_infinite_map_negative_chunks(parser_type):
    tiled_map = parse_map(_map_path(NESTED_GROUPS_INFINITE, parser_type))

    # Chunk at tile (-4, -4): its origin pixel is (-128, -128).
    result = get_effective_tile_layers(tiled_map, OrderedPair(-128, -128))
    assert [entry.name for entry in result] == ["Infinite Base"]
    assert result[0].gid == 1
    assert result[0].tile_coordinates == OrderedPair(-4, -4)

    # Nested Negative's tile sits at (-4, -4); the group offsets it down 32px.
    nested = get_effective_tile_layers(tiled_map, OrderedPair(-128, -96))
    assert [entry.name for entry in nested] == [
        "Infinite Base",
        "Nested Negative",
    ]
    nested_entry = nested[1]
    assert nested_entry.tile_coordinates == OrderedPair(-4, -4)
    assert nested_entry.opacity == pytest.approx(0.25)
    assert nested_entry.offset == OrderedPair(0, 32)
    # Group #0000ff * layer #ff0000
    assert nested_entry.tint_color == Color(0, 0, 0, 255)


@pytest.mark.parametrize("parser_type", ["json", "tmx"])
def test_infinite_map_flipped_gid(parser_type):
    tiled_map = parse_map(_map_path(NESTED_GROUPS_INFINITE, parser_type))

    # Nested Flipped GID at chunk cell (1, 1); group offset y=32 moves it to
    # pixel (32, 64).
    result = get_effective_tile_layers(tiled_map, OrderedPair(32, 64))
    flipped = next(entry for entry in result if entry.name == "Nested Flipped")
    assert flipped.gid == 2684354569
    assert flipped.gid_without_flags == 9
    assert flipped.flipped_horizontally is True
    assert flipped.flipped_vertically is False
    assert flipped.flipped_diagonally is True


@pytest.mark.parametrize("parser_type", ["json", "tmx"])
def test_infinite_map_misses_between_chunks(parser_type):
    tiled_map = parse_map(_map_path(NESTED_GROUPS_INFINITE, parser_type))

    # Row 4 is beyond the two 4x4 chunks covering y in [-4, 4).
    assert get_effective_tile_layers(tiled_map, OrderedPair(0, 128)) == []


def test_iter_effective_tile_layers_flattens_groups():
    tiled_map = parse_map(_map_path(NESTED_GROUPS, "tmx"))
    flattened = iter_effective_tile_layers(tiled_map.layers)

    # Hidden tile layers and non-tile layers are excluded; order is preserved.
    assert [layer.name for layer, _, _, _ in flattened] == [
        "Background",
        "Collision",
        "Empty Grandchild",
        "Lighting",
        "Empty Top",
        "Flipped",
        "Fractional Offset",
        "Negative Offset",
    ]
    collision = next(item for item in flattened if item[0].name == "Collision")
    _, opacity, offset, tint = collision
    assert opacity == pytest.approx(0.2)
    assert offset == OrderedPair(14, 28)
    assert tint == Color(0, 0, 255, 128)


def test_cyclic_group_raises():
    tiled_map = parse_map(_map_path(NESTED_GROUPS, "json"))

    outer = next(layer for layer in tiled_map.layers if layer.name == "Outer Group")
    # Hand-built cycle; parsed maps themselves can never contain one.
    outer.layers.append(outer)

    with pytest.raises(CyclicLayerGroupError):
        get_effective_tile_layers(tiled_map, OrderedPair(0, 0))

    with pytest.raises(CyclicLayerGroupError):
        iter_effective_tile_layers(tiled_map.layers)


def test_invalid_tint_raises():
    tiled_map = parse_map(_map_path(NESTED_GROUPS, "json"))
    background = next(layer for layer in tiled_map.layers if layer.name == "Background")
    background.tint_color = Color(256, 0, 0, 255)

    with pytest.raises(InvalidTintColorError):
        get_effective_tile_layers(tiled_map, OrderedPair(0, 0))

    with pytest.raises(InvalidTintColorError):
        iter_effective_tile_layers(tiled_map.layers)


def test_unsupported_orientation_raises():
    tiled_map = parse_map(_map_path(NESTED_GROUPS, "json"))
    tiled_map.orientation = "isometric"

    with pytest.raises(UnsupportedOrientationError):
        get_effective_tile_layers(tiled_map, OrderedPair(0, 0))


def test_parsed_objects_are_not_mutated():
    tiled_map = parse_map(_map_path(NESTED_GROUPS, "json"))

    def snapshot(layers):
        values = []
        for layer in layers:
            values.append(
                (
                    layer.name,
                    layer.opacity,
                    layer.offset,
                    layer.tint_color,
                    layer.visible,
                )
            )
            if isinstance(layer, LayerGroup) and layer.layers:
                values.extend(snapshot(layer.layers))
        return values

    before = snapshot(tiled_map.layers)
    get_effective_tile_layers(tiled_map, OrderedPair(36, 40))
    get_effective_tile_layers(tiled_map, OrderedPair(-50, 200))
    iter_effective_tile_layers(tiled_map.layers)
    assert snapshot(tiled_map.layers) == before


def test_entries_reference_original_layer_objects():
    tiled_map = parse_map(_map_path(NESTED_GROUPS, "json"))
    result = get_effective_tile_layers(tiled_map, OrderedPair(36, 40))
    background_layer = next(
        layer for layer in tiled_map.layers if layer.name == "Background"
    )
    assert result[0].layer is background_layer


def test_synthetic_chunk_layer_with_negative_origin():
    # Exercises the chunk lookup path directly, including negative origins,
    # without depending on a fixture file.
    chunk = Chunk(
        coordinates=OrderedPair(-2, -2),
        size=Size(2, 2),
        data=[[11, 12], [13, 14]],
    )
    tile_layer = TileLayer(
        name="Synthetic",
        chunks=[chunk],
        size=Size(2, 2),
    )
    group = LayerGroup(
        name="Synthetic Group",
        layers=[tile_layer],
        opacity=0.5,
        offset=OrderedPair(0, 32),
    )
    tiled_map = parse_map(_map_path(NESTED_GROUPS, "json"))
    tiled_map.layers = [group]

    result = get_effective_tile_layers(tiled_map, OrderedPair(-64, -32))
    assert len(result) == 1
    entry = result[0]
    assert entry.tile_coordinates == OrderedPair(-2, -2)
    assert entry.gid == 11
    assert entry.opacity == pytest.approx(0.5)
    assert entry.offset == OrderedPair(0, 32)


def test_point_accepts_plain_tuple():
    tiled_map = parse_map(_map_path(NESTED_GROUPS, "json"))
    result = get_effective_tile_layers(tiled_map, (36, 40))
    assert [entry.name for entry in result] == [
        "Background",
        "Collision",
        "Lighting",
        "Flipped",
    ]
