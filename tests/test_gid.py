"""Tests for the tile source (GID) lookup API in :mod:`pytiled_parser.gid`.

These tests cover:

* GID flag decoding and the eight flip combinations.
* Spritesheet (atlas) tilesets with margin and spacing, including the exact
  pixel crop rectangles.
* Image collection (per-tile image) tilesets, including cropped sub-rects.
* tileoffset handling.
* Empty GIDs, unknown GIDs, overlapping tileset ranges, missing images,
  ragged image dimensions and illegal GID values, all of which must produce
  a deterministic result or a contextual exception.
"""

import os
from pathlib import Path

import pytest

import pytiled_parser as p
from pytiled_parser import gid
from pytiled_parser.common_types import OrderedPair, Rect
from pytiled_parser.gid_exception import (
    GIDError,
    GIDNotFoundError,
    InvalidGIDError,
    TileImageNotFoundError,
    TileRectError,
    TilesetOverlapError,
)
from pytiled_parser.tileset import Tile, Tileset

TESTS_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
TEST_DATA = TESTS_DIR / "test_data"
TILE_SETS = TEST_DATA / "tilesets"

ATLAS_JSON = TILE_SETS / "image" / "tileset.json"
ATLAS_OFFSET_JSON = TILE_SETS / "image_tile_offset" / "tileset.json"
COLLECTION_JSON = TILE_SETS / "individual_images" / "tileset.json"
ATLAS_IMAGE = Path("../../images/tmw_desert_spacing.png")


@pytest.fixture
def atlas() -> Tileset:
    # firstgid=1, 48 tiles, 8 columns, tile 32x32, margin=1, spacing=1,
    # image 265x199.
    return p.parse_tileset(ATLAS_JSON)


@pytest.fixture
def offset_atlas() -> Tileset:
    return p.parse_tileset(ATLAS_OFFSET_JSON)


@pytest.fixture
def collection() -> Tileset:
    # firstgid=1, 5 individually imaged tiles.
    return p.parse_tileset(COLLECTION_JSON)


def make_tileset(
    firstgid: int,
    tile_count: int,
    columns: int,
    name: str = "synthetic",
    **kwargs,
) -> Tileset:
    """Small helper for building tilesets with custom geometry."""
    defaults = {
        "name": name,
        "tile_width": 32,
        "tile_height": 32,
        "tile_count": tile_count,
        "columns": columns,
        "firstgid": firstgid,
        "spacing": 0,
        "margin": 0,
    }
    defaults.update(kwargs)
    return Tileset(**defaults)


# ---------------------------------------------------------------------------
# decode_gid
# ---------------------------------------------------------------------------


def test_decode_gid_plain():
    plain, transform = gid.decode_gid(42)
    assert plain == 42
    assert transform == gid.GIDTransform(False, False, False)


def test_decode_gid_zero_is_empty_cell():
    plain, transform = gid.decode_gid(0)
    assert plain == 0
    assert transform == gid.GIDTransform(False, False, False)


def test_decode_gid_strips_all_three_flags():
    raw = 17 | gid.FLIP_HORIZONTALLY_FLAG | gid.FLIP_VERTICALLY_FLAG
    raw |= gid.FLIP_DIAGONALLY_FLAG
    plain, transform = gid.decode_gid(raw)
    assert plain == 17
    assert transform == gid.GIDTransform(True, True, True)


def test_decode_gid_individual_flag_bits():
    assert gid.decode_gid(5 | gid.FLIP_HORIZONTALLY_FLAG)[1] == (
        gid.GIDTransform(True, False, False)
    )
    assert gid.decode_gid(5 | gid.FLIP_VERTICALLY_FLAG)[1] == (
        gid.GIDTransform(False, True, False)
    )
    assert gid.decode_gid(5 | gid.FLIP_DIAGONALLY_FLAG)[1] == (
        gid.GIDTransform(False, False, True)
    )


@pytest.mark.parametrize("bad_gid", [-1, -100, 0x100000000, 0xFFFFFFFFF])
def test_decode_gid_rejects_out_of_range(bad_gid):
    with pytest.raises(InvalidGIDError) as exc:
        gid.decode_gid(bad_gid)
    assert exc.value.gid == bad_gid


def test_decode_gid_rejects_hexagonal_rotation_flag():
    bad = 5 | gid.ROTATED_HEXAGONAL_120_FLAG
    with pytest.raises(InvalidGIDError) as exc:
        gid.decode_gid(bad)
    assert exc.value.gid == bad


def test_decode_gid_rejects_zero_with_flags():
    bad = gid.FLIP_HORIZONTALLY_FLAG
    with pytest.raises(InvalidGIDError) as exc:
        gid.decode_gid(bad)
    assert exc.value.gid == bad


def test_decode_gid_rejects_non_integer():
    with pytest.raises(InvalidGIDError):
        gid.decode_gid(1.5)  # type: ignore[arg-type]


def test_max_plain_gid_fits_mask():
    plain, _ = gid.decode_gid(gid.GID_MASK)
    assert plain == gid.GID_MASK


# ---------------------------------------------------------------------------
# Spritesheet (atlas) geometry with margin and spacing
# ---------------------------------------------------------------------------


def test_atlas_first_tile_rect(atlas):
    source = gid.get_tile_source_from_tileset(1, atlas)
    assert source.local_id == 0
    assert source.image_path == ATLAS_IMAGE
    # margin of 1 pixel on every side.
    assert source.rect == Rect(1, 1, 32, 32)


def test_atlas_rects_account_for_margin_and_spacing(atlas):
    # pitch between tile origins is tile size + spacing = 33.
    expected = {
        1: Rect(1, 1, 32, 32),  # local 0 -> col 0 row 0
        2: Rect(34, 1, 32, 32),  # local 1 -> col 1 row 0
        8: Rect(1 + 7 * 33, 1, 32, 32),  # local 7 -> col 7 row 0
        9: Rect(1, 34, 32, 32),  # local 8 -> col 0 row 1
        10: Rect(34, 34, 32, 32),  # local 9 -> col 1 row 1
    }
    for raw_gid, rect in expected.items():
        source = gid.get_tile_source_from_tileset(raw_gid, atlas)
        assert source.rect == rect
        assert source.plain_gid == raw_gid


def test_atlas_last_tile_in_grid(atlas):
    # local 47 -> col 7, row 5
    last_gid = atlas.firstgid + atlas.tile_count - 1
    source = gid.get_tile_source_from_tileset(last_gid, atlas)
    assert source.local_id == 47
    assert source.rect == Rect(1 + 7 * 33, 1 + 5 * 33, 32, 32)
    # last corner must still be inside the 265x199 image:
    assert source.rect.x + source.rect.width <= atlas.image_width
    assert source.rect.y + source.rect.height <= atlas.image_height


def test_zero_margin_zero_spacing_grid():
    ts = make_tileset(
        firstgid=1,
        tile_count=6,
        columns=3,
        image=Path("sheet.png"),
        image_width=96,
        image_height=64,
    )
    source = gid.get_tile_source_from_tileset(5, ts)  # local 4 -> col 1 row 1
    assert source.rect == Rect(32, 32, 32, 32)
    assert source.image_path == Path("sheet.png")


def test_custom_margin_and_spacing():
    ts = make_tileset(
        firstgid=10,
        tile_count=4,
        columns=2,
        margin=4,
        spacing=2,
        image=Path("sheet.png"),
        image_width=80,
        image_height=80,
    )
    # local 3 -> col 1, row 1: x = 4 + 1*(32+2) = 38, y = 38
    source = gid.get_tile_source_from_tileset(13, ts)
    assert source.local_id == 3
    assert source.rect == Rect(38, 38, 32, 32)


def test_tile_offset_reported(offset_atlas):
    source = gid.get_tile_source_from_tileset(1, offset_atlas)
    assert offset_atlas.tile_offset == OrderedPair(3, 5)
    assert source.tile_offset == OrderedPair(3, 5)


def test_tile_offset_defaults_to_zero(atlas):
    assert atlas.tile_offset is None
    source = gid.get_tile_source_from_tileset(1, atlas)
    assert source.tile_offset == OrderedPair(0, 0)


# ---------------------------------------------------------------------------
# Image collection (one image per tile)
# ---------------------------------------------------------------------------


def test_collection_returns_per_tile_image(collection):
    source = gid.get_tile_source_from_tileset(1, collection)
    assert source.local_id == 0
    assert source.image_path == Path("../../images/tile_01.png")
    assert source.rect == Rect(0, 0, 32, 32)
    assert source.tileset is collection


def test_collection_each_tile_has_own_image(collection):
    for local_id in range(collection.tile_count):
        source = gid.get_tile_source_from_tileset(
            collection.firstgid + local_id, collection
        )
        assert source.local_id == local_id
        tile = collection.tiles[local_id]
        assert source.image_path == tile.image
        assert source.rect == Rect(tile.x, tile.y, tile.width, tile.height)


def test_collection_sub_rectangle_tile(collection):
    # local id 4 uses a 64px wide image but declares a 32x32 crop at x=32.
    source = gid.get_tile_source_from_tileset(5, collection)
    assert source.image_path == Path("../../images/tile_05.png")
    assert source.rect == Rect(32, 0, 32, 32)


def test_collection_missing_tile_entry():
    ts = make_tileset(firstgid=1, tile_count=3, columns=0, tiles={})
    with pytest.raises(TileImageNotFoundError) as exc:
        gid.get_tile_source_from_tileset(2, ts)
    assert exc.value.local_id == 1
    assert exc.value.gid == 2
    assert exc.value.tileset is ts


def test_collection_tile_id_absent_from_populated_dict():
    # tiles dict exists but does not contain the requested local ID.
    ts = make_tileset(
        firstgid=1,
        tile_count=4,
        columns=0,
        tiles={0: Tile(id=0, image=Path("a.png"), width=32, height=32)},
    )
    with pytest.raises(TileImageNotFoundError) as exc:
        gid.get_tile_source_from_tileset(3, ts)  # local ID 2 missing
    assert exc.value.local_id == 2
    assert exc.value.gid == 3


def test_collection_tile_without_image():
    ts = make_tileset(
        firstgid=1,
        tile_count=2,
        columns=0,
        tiles={0: Tile(id=0, width=32, height=32)},
    )
    with pytest.raises(TileImageNotFoundError) as exc:
        gid.get_tile_source_from_tileset(1, ts)
    assert exc.value.local_id == 0


def test_tileset_without_any_image():
    ts = make_tileset(firstgid=1, tile_count=2, columns=1)
    with pytest.raises(TileImageNotFoundError):
        gid.get_tile_source_from_tileset(1, ts)


# ---------------------------------------------------------------------------
# The eight flip combinations
# ---------------------------------------------------------------------------

EIGHT_COMBOS = [
    (0, gid.GIDTransform(False, False, False)),
    (gid.FLIP_HORIZONTALLY_FLAG, gid.GIDTransform(True, False, False)),
    (gid.FLIP_VERTICALLY_FLAG, gid.GIDTransform(False, True, False)),
    (
        gid.FLIP_HORIZONTALLY_FLAG | gid.FLIP_VERTICALLY_FLAG,
        gid.GIDTransform(True, True, False),
    ),
    (gid.FLIP_DIAGONALLY_FLAG, gid.GIDTransform(False, False, True)),
    (
        gid.FLIP_HORIZONTALLY_FLAG | gid.FLIP_DIAGONALLY_FLAG,
        gid.GIDTransform(True, False, True),
    ),
    (
        gid.FLIP_VERTICALLY_FLAG | gid.FLIP_DIAGONALLY_FLAG,
        gid.GIDTransform(False, True, True),
    ),
    (
        gid.FLIP_HORIZONTALLY_FLAG
        | gid.FLIP_VERTICALLY_FLAG
        | gid.FLIP_DIAGONALLY_FLAG,
        gid.GIDTransform(True, True, True),
    ),
]


@pytest.mark.parametrize("flag_bits,expected_transform", EIGHT_COMBOS)
def test_eight_flip_combinations_atlas(atlas, flag_bits, expected_transform):
    raw_gid = 10 | flag_bits
    source = gid.get_tile_source_from_tileset(raw_gid, atlas)

    # flags are decoded...
    assert source.transform == expected_transform
    # ...while the plain GID, tile and crop rect are unaffected by flips.
    assert source.plain_gid == 10
    assert source.local_id == 9
    assert source.rect == Rect(34, 34, 32, 32)
    # the original raw GID is reported verbatim.
    assert source.gid == raw_gid


@pytest.mark.parametrize("flag_bits,expected_transform", EIGHT_COMBOS)
def test_eight_flip_combinations_collection(collection, flag_bits, expected_transform):
    raw_gid = 3 | flag_bits
    source = gid.get_tile_source_from_tileset(raw_gid, collection)
    assert source.transform == expected_transform
    assert source.plain_gid == 3
    assert source.local_id == 2
    assert source.image_path == Path("../../images/tile_03.png")
    assert source.gid == raw_gid


def test_raw_gid_never_mutated(atlas):
    raw_gid = 7 | gid.FLIP_VERTICALLY_FLAG | gid.FLIP_DIAGONALLY_FLAG
    snapshot = raw_gid
    gid.get_tile_source_from_tileset(raw_gid, atlas)
    gid.decode_gid(raw_gid)
    assert raw_gid == snapshot


# ---------------------------------------------------------------------------
# Empty cells, unknown GIDs and range boundaries
# ---------------------------------------------------------------------------


def test_gid_zero_returns_none(atlas):
    assert gid.get_tile_source_from_tileset(0, atlas) is None
    assert gid.get_tile_source(0, {atlas.firstgid: atlas}) is None


def test_gid_below_firstgid_not_found():
    ts = make_tileset(firstgid=100, tile_count=4, columns=2)
    with pytest.raises(GIDNotFoundError) as exc:
        gid.get_tile_source(1, {100: ts})
    assert exc.value.gid == 1
    assert exc.value.plain_gid == 1


def test_gid_above_tileset_range_not_found(atlas):
    # atlas covers GIDs 1..48
    with pytest.raises(GIDNotFoundError) as exc:
        gid.get_tile_source(49, {1: atlas})
    assert exc.value.plain_gid == 49


def test_gid_not_found_lists_known_firstgids(atlas):
    other = make_tileset(firstgid=100, tile_count=4, columns=2)
    with pytest.raises(GIDNotFoundError) as exc:
        gid.get_tile_source(60, {1: atlas, 100: other})
    message = str(exc.value)
    assert "1" in message and "100" in message


def test_tileset_boundary_gids(atlas):
    first = gid.get_tile_source_from_tileset(atlas.firstgid, atlas)
    assert first.local_id == 0
    last = gid.get_tile_source_from_tileset(
        atlas.firstgid + atlas.tile_count - 1, atlas
    )
    assert last.local_id == atlas.tile_count - 1


def test_non_firstgid_one_tileset():
    ts = make_tileset(
        firstgid=100,
        tile_count=4,
        columns=2,
        image=Path("sheet.png"),
        image_width=64,
        image_height=64,
    )
    source = gid.get_tile_source_from_tileset(103, ts)
    assert source.local_id == 3
    assert source.plain_gid == 103


# ---------------------------------------------------------------------------
# Overlapping tileset ranges
# ---------------------------------------------------------------------------


def test_overlapping_tileset_ranges_raise_with_context():
    low = make_tileset(
        firstgid=1,
        tile_count=20,
        columns=4,
        name="low",
        image=Path("low.png"),
        image_width=128,
        image_height=128,
    )
    high = make_tileset(
        firstgid=10,
        tile_count=10,
        columns=2,
        name="high",
        image=Path("high.png"),
        image_width=64,
        image_height=160,
    )
    tilesets = {1: low, 10: high}

    with pytest.raises(TilesetOverlapError) as exc:
        gid.get_tile_source(12, tilesets)

    # deterministic selection: highest firstgid wins
    assert exc.value.selected_tileset is high
    assert exc.value.candidates[0] is high
    assert exc.value.candidates[1] is low
    assert exc.value.plain_gid == 12
    assert exc.value.gid == 12
    assert "high" in str(exc.value) and "low" in str(exc.value)


def test_overlap_resolves_deterministically_identical_to_highest_firstgid():
    low = make_tileset(
        firstgid=1,
        tile_count=20,
        columns=4,
        name="low",
        image=Path("low.png"),
        image_width=128,
        image_height=128,
    )
    high = make_tileset(
        firstgid=10,
        tile_count=10,
        columns=2,
        name="high",
        image=Path("high.png"),
        image_width=64,
        image_height=160,
    )
    tilesets = {1: low, 10: high}

    with pytest.raises(TilesetOverlapError) as exc:
        gid.get_tile_source(12, tilesets)

    direct = gid.get_tile_source_from_tileset(12, high)
    chosen = gid.get_tile_source_from_tileset(12, exc.value.selected_tileset)
    assert chosen.rect == direct.rect
    assert chosen.image_path == direct.image_path
    assert chosen.local_id == 2  # 12 - 10


def test_non_overlapping_tilesets_resolve_normally():
    first = make_tileset(
        firstgid=1,
        tile_count=4,
        columns=2,
        name="first",
        image=Path("a.png"),
        image_width=64,
        image_height=64,
    )
    second = make_tileset(
        firstgid=100,
        tile_count=4,
        columns=2,
        name="second",
        image=Path("b.png"),
        image_width=64,
        image_height=64,
    )
    tilesets = {1: first, 100: second}

    source = gid.get_tile_source(101, tilesets)
    assert source.tileset is second
    assert source.local_id == 1

    source = gid.get_tile_source(2, tilesets)
    assert source.tileset is first


# ---------------------------------------------------------------------------
# Ragged / malformed spritesheets
# ---------------------------------------------------------------------------


def test_ragged_image_rect_outside_bounds_raises():
    # 2x2 grid of 32px tiles needs a 64x64 image, declare one that is too
    # short so the second row falls outside of it.
    ts = make_tileset(
        firstgid=1,
        tile_count=4,
        columns=2,
        image=Path("ragged.png"),
        image_width=64,
        image_height=40,
    )
    with pytest.raises(TileRectError) as exc:
        gid.get_tile_source_from_tileset(3, ts)  # local 2 -> row 1
    assert exc.value.rect == Rect(0, 32, 32, 32)
    assert exc.value.local_id == 2
    assert "ragged.png" in str(exc.value)


def test_ragged_image_without_declared_dimensions_still_computes():
    # When image dimensions are unknown there is nothing to check against,
    # the mathematical rectangle is returned deterministically.
    ts = make_tileset(
        firstgid=1,
        tile_count=4,
        columns=2,
        image=Path("unknown_size.png"),
    )
    source = gid.get_tile_source_from_tileset(3, ts)
    assert source.rect == Rect(0, 32, 32, 32)


def test_zero_columns_raises():
    ts = make_tileset(
        firstgid=1,
        tile_count=4,
        columns=0,
        image=Path("sheet.png"),
        image_width=64,
        image_height=64,
    )
    with pytest.raises(TileRectError) as exc:
        gid.get_tile_source_from_tileset(1, ts)
    assert exc.value.tileset is ts


# ---------------------------------------------------------------------------
# TiledMap integration and the Tileset convenience method
# ---------------------------------------------------------------------------


def test_get_tile_source_accepts_tiled_map(atlas, collection):
    # Standalone parsed tilesets always receive firstgid=1; re-key the
    # collection to a non-overlapping range to simulate map-level tilesets.
    collection.firstgid = 100
    tiled_map = p.TiledMap(
        map_file=Path("map.json"),
        infinite=False,
        layers=[],
        map_size=p.Size(8, 8),
        next_layer_id=None,
        next_object_id=1,
        orientation="orthogonal",
        render_order="right-down",
        tiled_version="1.9",
        tile_size=p.Size(32, 32),
        tilesets={1: atlas, 100: collection},
        version="1.9",
    )
    source = gid.get_tile_source(102, tiled_map)
    assert source.tileset is collection
    assert source.local_id == 2
    # TiledMap and dict inputs must behave identically.
    source_dict = gid.get_tile_source(102, tiled_map.tilesets)
    assert source_dict == source


def test_tileset_convenience_method(atlas):
    source = atlas.get_tile_source(9)
    assert source.rect == Rect(1, 34, 32, 32)
    assert atlas.get_tile_source(0) is None


def test_tileset_convenience_method_rejects_foreign_gid(atlas, collection):
    collection.firstgid = 100
    with pytest.raises(GIDNotFoundError):
        atlas.get_tile_source(collection.firstgid)


def test_flip_flags_survive_map_level_lookup(atlas):
    raw = 5 | gid.FLIP_HORIZONTALLY_FLAG | gid.FLIP_DIAGONALLY_FLAG
    source = gid.get_tile_source(raw, {1: atlas})
    assert source.gid == raw
    assert source.plain_gid == 5
    assert source.transform == gid.GIDTransform(True, False, True)


def test_all_gid_errors_share_base_class():
    for exc_type in (
        InvalidGIDError,
        GIDNotFoundError,
        TilesetOverlapError,
        TileImageNotFoundError,
        TileRectError,
    ):
        assert issubclass(exc_type, GIDError)
