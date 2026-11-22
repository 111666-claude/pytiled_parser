"""Tests for viewport tile queries on finite and infinite tile layers.

The same assertions run against the JSON and TMX versions of a fixed map in
``tests/test_data/viewport``. That map intentionally mixes an infinite chunk
layer (negative coordinates, non-square chunks, overlapping chunks, flip
flags), a finite layer and a nested group layer. Additional unit tests build
layers directly to cover cases that are awkward to express in a map file.
"""

import os
from pathlib import Path

import pytest

from pytiled_parser import (
    Chunk,
    OrderedPair,
    Size,
    TileLayer,
    TileRect,
    ViewportTile,
    get_viewport_tiles,
    iter_viewport_tiles,
    parse_map,
)
from pytiled_parser.layer import LayerGroup
from pytiled_parser.viewport import (
    _is_empty_gid,
    _normalize_rect,
    _normalize_source,
)

TESTS_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
VIEWPORT_DATA = TESTS_DIR / "test_data" / "viewport"


@pytest.fixture(params=["json", "tmx"])
def tiled_map(request):
    return parse_map(VIEWPORT_DATA / f"map.{request.param}")


@pytest.fixture(params=["json", "tmx"])
def chunk_layer(request):
    tiled_map = parse_map(VIEWPORT_DATA / f"map.{request.param}")
    return tiled_map.layers[0]


@pytest.mark.parametrize(
    "rect,tile", [((-8, -8, 1, 1), 10), ((-8, -8, 8, 4), 14), ((0, -8, 2, 1), 99)]
)
def test_fixture_chunk_layer(chunk_layer, rect, tile):
    assert tile in [result.gid for result in get_viewport_tiles(chunk_layer, rect)]


def test_whole_map_matches_between_formats(tiled_map):
    results = get_viewport_tiles(tiled_map, TileRect(-8, -8, 20, 12))

    assert all(isinstance(result, ViewportTile) for result in results)
    assert [
        (result.layer_name, result.coordinates, result.gid) for result in results
    ] == [
        ("Chunks", OrderedPair(-8, -8), 10),
        ("Chunks", OrderedPair(0, -8), 99),
        ("Chunks", OrderedPair(2, -8), 32),
        ("Chunks", OrderedPair(4, -8), 40),
        ("Chunks", OrderedPair(-7, -7), 11),
        ("Chunks", OrderedPair(3, -7), 2147483649),
        ("Chunks", OrderedPair(5, -7), 55),
        ("Chunks", OrderedPair(-6, -6), 12),
        ("Chunks", OrderedPair(4, -6), 45),
        ("Chunks", OrderedPair(-8, -5), 14),
        ("Chunks", OrderedPair(-5, -5), 13),
        ("Chunks", OrderedPair(7, -5), 46),
        ("Chunks", OrderedPair(-8, -3), 20),
        ("Chunks", OrderedPair(11, -1), 536870917),
        ("Finite", OrderedPair(0, 0), 1),
        ("Finite", OrderedPair(2, 0), 2),
        ("Finite", OrderedPair(4, 0), 3),
        ("Finite", OrderedPair(1, 1), 4),
        ("Finite", OrderedPair(5, 1), 5),
        ("Finite", OrderedPair(0, 2), 6),
        ("Finite", OrderedPair(3, 2), 7),
        ("Nested", OrderedPair(0, 0), 7),
        ("Nested", OrderedPair(1, 1), 7),
    ]


def test_layer_order_follows_draw_order(tiled_map):
    results = get_viewport_tiles(tiled_map, TileRect(-8, -8, 20, 12))
    names = [result.layer_name for result in results]

    # Layers appear in document order, with group children expanded in place.
    assert names == sorted(names, key=["Chunks", "Finite", "Nested"].index)


def test_results_grouped_row_major_within_layer(tiled_map):
    results = get_viewport_tiles(tiled_map, TileRect(-8, -8, 20, 12))
    for layer_name in ("Chunks", "Finite", "Nested"):
        cells = [
            (result.coordinates.y, result.coordinates.x)
            for result in results
            if result.layer_name == layer_name
        ]
        assert cells == sorted(cells)


def test_storage_order_does_not_change_row_major_output():
    # Non-overlapping chunks in a shuffled list still come back row by row.
    chunks = [
        Chunk(OrderedPair(4, 0), Size(2, 2), [[5, 6], [7, 8]]),
        Chunk(OrderedPair(-2, -2), Size(2, 2), [[1, 2], [3, 4]]),
        Chunk(OrderedPair(0, 0), Size(2, 2), [[9, 10], [11, 12]]),
    ]
    forward = TileLayer(name="a", chunks=chunks)
    reverse = TileLayer(name="a", chunks=list(reversed(chunks)))

    def cells(layer):
        return [
            (r.coordinates.x, r.coordinates.y, r.gid)
            for r in get_viewport_tiles(layer, TileRect(-2, -2, 8, 4))
        ]

    assert (
        cells(forward)
        == cells(reverse)
        == [
            (-2, -2, 1),
            (-1, -2, 2),
            (-2, -1, 3),
            (-1, -1, 4),
            (0, 0, 9),
            (1, 0, 10),
            (4, 0, 5),
            (5, 0, 6),
            (0, 1, 11),
            (1, 1, 12),
            (4, 1, 7),
            (5, 1, 8),
        ]
    )


# ---------------------------------------------------------------------------
# Negative coordinates and rectangular clipping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rect,expected",
    [
        ((-8, -8, 1, 1), [(-8, -8)]),
        ((-7, -7, 1, 1), [(-7, -7)]),
        # Top row of the second negative chunk is empty.
        ((-8, -4, 1, 1), []),
        ((-8, -3, 1, 1), [(-8, -3)]),
        ((-6, -6, 1, 1), [(-6, -6)]),
        # (-9, -9, 2, 2) reaches exactly one populated cell at (-8, -8).
        ((-9, -9, 2, 2), [(-8, -8)]),
        ((-9, -9, 1, 1), []),
        ((11, -1, 1, 1), [(11, -1)]),
        ((5, -8, 1, 1), []),
        # (7, -5) is the only populated cell in that area, and a 1x1 viewport
        # at x=6 must not reach it.
        ((6, -5, 1, 1), []),
    ],
)
def test_negative_coordinate_bounds(tiled_map, rect, expected):
    results = get_viewport_tiles(tiled_map, rect)
    chunk_cells = [
        (result.coordinates.x, result.coordinates.y)
        for result in results
        if result.layer_name == "Chunks"
    ]
    assert chunk_cells == expected


def test_rectangle_is_half_open(tiled_map):
    # The single tile at (-8, -8) is included; the rectangle stops just after.
    assert len(get_viewport_tiles(tiled_map, (-8, -8, 1, 1))) == 1
    assert get_viewport_tiles(tiled_map, (-9, -9, 1, 1)) == []


def test_non_square_chunk_clipped(tiled_map):
    # The chunk at (0, -8) is 12 wide and 8 tall. A 1x1 viewport must not
    # expand the other 95 cells of that chunk.
    results = get_viewport_tiles(tiled_map.layers[0], TileRect(11, -1, 1, 1))
    assert [(r.coordinates, r.gid) for r in results] == [
        (OrderedPair(11, -1), 536870917)
    ]


def test_negative_chunk_and_window_overlap(tiled_map):
    # A viewport that starts above and left of the chunked area must return
    # exactly the same tiles as the tightly fitted rectangle.
    fitted = get_viewport_tiles(tiled_map, TileRect(-8, -8, 20, 12))
    oversized = get_viewport_tiles(tiled_map, TileRect(-100, -100, 112, 104))
    assert oversized == fitted


# ---------------------------------------------------------------------------
# Overlapping chunks
# ---------------------------------------------------------------------------


def test_overlapping_chunk_priority_later_wins(tiled_map):
    # The 12 wide chunk at (0, -8) places 30/31/32 at (0..2, -8); the later
    # 2x1 chunk at (0, -8) places 99/0 over the first two cells, so the
    # first cell becomes 99 and the second cell is cleared to empty.
    results = get_viewport_tiles(tiled_map.layers[0], TileRect(0, -8, 3, 1))
    assert [(r.coordinates.x, r.gid) for r in results] == [(0, 99), (2, 32)]


def test_overlapping_chunk_empty_clears_earlier(tiled_map):
    # The later chunk at (4, -7) stores 55 at its (5, -7) cell, overriding the
    # 41 that the earlier chunk put at the same location.
    results = get_viewport_tiles(tiled_map.layers[0], TileRect(4, -8, 4, 4))
    assert [(r.coordinates.x, r.coordinates.y, r.gid) for r in results] == [
        (4, -8, 40),
        (5, -7, 55),
        (4, -6, 45),
        (7, -5, 46),
    ]


def test_overlapping_chunk_winner_is_reported(tiled_map):
    results = get_viewport_tiles(tiled_map.layers[0], TileRect(0, -8, 3, 1))
    # The first two cells are owned by the small later chunk; the third
    # survives from the large earlier chunk.
    assert results[0].chunk.size == Size(2, 1)
    assert results[1].chunk.size == Size(12, 8)
    assert results[1].gid == 32


# ---------------------------------------------------------------------------
# Flip flags
# ---------------------------------------------------------------------------


def test_flip_flags_preserved(tiled_map):
    results = get_viewport_tiles(tiled_map.layers[0], TileRect(3, -7, 1, 1))
    assert results[0].gid == 2147483649

    results = get_viewport_tiles(tiled_map.layers[0], TileRect(11, -1, 1, 1))
    assert results[0].gid == 536870917


def test_flip_flags_alone_are_not_empty():
    # Flag bits live in the top four bits; a cell carrying only flags still
    # refers to tile 0 and must be treated as empty.
    assert _is_empty_gid(0)
    assert not _is_empty_gid(1)
    assert not _is_empty_gid(0x80000001)
    assert _is_empty_gid(0x80000000)
    assert _is_empty_gid(0xE0000000)
    # Hexagonal maps use the 0x10000000 bit for a 120 degree rotation.
    assert _is_empty_gid(0x10000000)
    assert not _is_empty_gid(0x10000009)


# ---------------------------------------------------------------------------
# Degenerate and huge viewports
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rect", [(0, 0, 0, 5), (0, 0, 5, 0), (0, 0, -3, 5), (0, 0, 5, -3)]
)
def test_empty_rect_returns_nothing(tiled_map, rect):
    assert get_viewport_tiles(tiled_map, rect) == []
    assert list(iter_viewport_tiles(tiled_map, rect)) == []


def test_enormous_sparse_viewport_is_cheap(tiled_map):
    # This rectangle spans billions of cells. A correct implementation clamps
    # it to the data that exists and returns immediately.
    results = get_viewport_tiles(
        tiled_map, TileRect(-(10**9), -(10**9), 2 * 10**9, 2 * 10**9)
    )
    assert len(results) == 25
    assert results == get_viewport_tiles(tiled_map, TileRect(-8, -8, 20, 13))


def test_viewport_outside_data_is_empty(tiled_map):
    assert get_viewport_tiles(tiled_map, TileRect(1000, 1000, 10, 10)) == []


# ---------------------------------------------------------------------------
# Type and validation behaviour
# ---------------------------------------------------------------------------


def test_tile_layer_convenience_methods(chunk_layer):
    rect = TileRect(-8, -8, 2, 2)
    assert chunk_layer.get_viewport_tiles(rect) == list(
        chunk_layer.iter_viewport_tiles(rect)
    )
    assert chunk_layer.get_viewport_tiles(rect) == get_viewport_tiles(chunk_layer, rect)


def test_rect_accepts_sequence_and_object(tiled_map):
    expected = get_viewport_tiles(tiled_map, TileRect(-8, -8, 3, 3))
    assert get_viewport_tiles(tiled_map, (-8, -8, 3, 3)) == expected
    assert get_viewport_tiles(tiled_map, [-8, -8, 3, 3]) == expected

    class Rect:
        x, y, width, height = -8, -8, 3, 3

    assert get_viewport_tiles(tiled_map, Rect()) == expected


@pytest.mark.parametrize("bad", [(0, 0, "a", 1), (0, 0, 1.5, 1), (True, 0, 1, 1)])
def test_bad_rect_raises_type_error(tiled_map, bad):
    with pytest.raises(TypeError):
        get_viewport_tiles(tiled_map, bad)


def test_bad_rect_type_raises():
    with pytest.raises(TypeError):
        _normalize_rect(object())


def test_bad_source_raises():
    with pytest.raises(TypeError):
        _normalize_source(object())
    with pytest.raises(TypeError):
        _normalize_source([object()])


def test_layer_group_source(tiled_map):
    group = tiled_map.layers[2]
    assert isinstance(group, LayerGroup)
    results = get_viewport_tiles(group, TileRect(0, 0, 2, 2))
    assert [result.gid for result in results] == [7, 7]


def test_iterable_layer_source(tiled_map):
    results = get_viewport_tiles(list(tiled_map.layers), TileRect(0, 0, 2, 2))
    assert [result.layer_name for result in results] == [
        "Finite",
        "Finite",
        "Nested",
        "Nested",
    ]


# ---------------------------------------------------------------------------
# Directly built layers: missing / malformed data must not break queries
# ---------------------------------------------------------------------------


def test_rect_accepts_integral_floats(tiled_map):
    expected = get_viewport_tiles(tiled_map, TileRect(-8, -8, 3, 3))
    assert get_viewport_tiles(tiled_map, (-8.0, -8.0, 3.0, 3.0)) == expected


def test_finite_layer_with_none_coordinates():
    layer = TileLayer(name="none coordinates", data=[[1]], coordinates=None)
    results = get_viewport_tiles(layer, (0, 0, 1, 1))
    assert [r.gid for r in results] == [1]


def test_finite_layer_with_empty_data():
    layer = TileLayer(name="empty data", data=[])
    assert get_viewport_tiles(layer, (0, 0, 1, 1)) == []


def test_finite_layer_with_empty_row():
    layer = TileLayer(name="empty row", data=[[], [1]])
    assert [
        (r.coordinates.y, r.gid) for r in get_viewport_tiles(layer, (0, 0, 3, 3))
    ] == [(1, 1)]


def test_chunk_with_missing_coordinates_is_skipped():
    layer = TileLayer(
        name="bad chunk",
        chunks=[Chunk(coordinates=None, size=Size(1, 1), data=[[1]])],  # type: ignore[arg-type]
    )
    assert get_viewport_tiles(layer, (0, 0, 1, 1)) == []


def test_chunk_with_empty_data_is_skipped():
    layer = TileLayer(
        name="bad chunk",
        chunks=[Chunk(coordinates=OrderedPair(0, 0), size=Size(1, 1), data=[])],
    )
    assert get_viewport_tiles(layer, (0, 0, 1, 1)) == []


def test_chunk_without_size_uses_data_extent():
    layer = TileLayer(
        name="no size",
        chunks=[Chunk(coordinates=OrderedPair(0, 0), size=None, data=[[1, 2]])],  # type: ignore[arg-type]
    )
    assert [r.gid for r in get_viewport_tiles(layer, (0, 0, 5, 5))] == [1, 2]


def test_none_chunk_in_list_is_skipped():
    layer = TileLayer(
        name="none chunk",
        chunks=[None, Chunk(coordinates=OrderedPair(0, 0), size=Size(1, 1), data=[[1]])],  # type: ignore[list-item]
    )
    assert [r.gid for r in get_viewport_tiles(layer, (0, 0, 1, 1))] == [1]


def test_non_tile_layers_are_ignored():
    # A group holding only non-tile layers flattens to nothing.
    group = LayerGroup(name="group", layers=[])
    assert get_viewport_tiles(group, (0, 0, 1, 1)) == []


def test_layer_group_cycle_does_not_recurse_forever():
    group = LayerGroup(name="loop", layers=[])
    group.layers.append(group)
    assert get_viewport_tiles(group, (0, 0, 1, 1)) == []


def test_chunk_iter_helper_handles_no_chunks():
    from pytiled_parser.viewport import _iter_chunk_layer

    layer = TileLayer(name="no chunks", chunks=None)
    assert list(_iter_chunk_layer(layer, TileRect(0, 0, 1, 1))) == []


def test_layer_with_empty_chunk_list_is_empty():
    layer = TileLayer(name="empty", chunks=[])
    assert get_viewport_tiles(layer, TileRect(0, 0, 10, 10)) == []


def test_ragged_finite_data_is_clamped():
    layer = TileLayer(name="ragged", data=[[1, 2], [3]])
    assert [
        (r.coordinates.x, r.coordinates.y, r.gid)
        for r in get_viewport_tiles(layer, (0, 0, 10, 10))
    ] == [
        (0, 0, 1),
        (1, 0, 2),
        (0, 1, 3),
    ]


def test_truncated_chunk_data_is_clamped():
    layer = TileLayer(
        name="truncated",
        chunks=[
            Chunk(
                coordinates=OrderedPair(0, 0),
                size=Size(4, 4),
                data=[[1, 2], [3]],
            )
        ],
    )
    assert [
        (r.coordinates.x, r.coordinates.y, r.gid)
        for r in get_viewport_tiles(layer, (-5, -5, 20, 20))
    ] == [
        (0, 0, 1),
        (1, 0, 2),
        (0, 1, 3),
    ]


def test_declared_chunk_size_limits_reads():
    # A chunk that claims to be 2x2 but carries more data must not expose the
    # extra cells, matching the declared chunk rectangle.
    layer = TileLayer(
        name="oversized",
        chunks=[
            Chunk(
                coordinates=OrderedPair(0, 0),
                size=Size(2, 2),
                data=[[1, 2, 3], [4, 5, 6], [7, 8, 9]],
            )
        ],
    )
    assert [r.gid for r in get_viewport_tiles(layer, (0, 0, 10, 10))] == [1, 2, 4, 5]


def test_negative_chunk_coordinates_direct():
    layer = TileLayer(
        name="negative",
        chunks=[
            Chunk(
                coordinates=OrderedPair(-3, -2),
                size=Size(2, 2),
                data=[[1, 2], [3, 4]],
            )
        ],
    )
    results = get_viewport_tiles(layer, (-2, -1, 2, 2))
    assert [(r.coordinates.x, r.coordinates.y, r.gid) for r in results] == [
        (-2, -1, 4),
    ]


def test_finite_layer_coordinates_offset():
    layer = TileLayer(
        name="offset", data=[[1, 2], [3, 4]], coordinates=OrderedPair(-10, 5)
    )
    results = get_viewport_tiles(layer, (-10, 5, 2, 2))
    assert [(r.coordinates.x, r.coordinates.y, r.gid) for r in results] == [
        (-10, 5, 1),
        (-9, 5, 2),
        (-10, 6, 3),
        (-9, 6, 4),
    ]
    # A rectangle in the un-offset coordinate space does not match.
    assert get_viewport_tiles(layer, (0, 0, 2, 2)) == []


def test_ragged_chunk_rows_are_clamped():
    layer = TileLayer(
        name="ragged",
        chunks=[
            Chunk(
                coordinates=OrderedPair(0, 0),
                size=Size(3, 3),
                data=[[1], [], [2, 3]],
            )
        ],
    )
    assert [
        (r.coordinates.x, r.coordinates.y, r.gid)
        for r in get_viewport_tiles(layer, (0, 0, 5, 5))
    ] == [
        (0, 0, 1),
        (0, 2, 2),
        (1, 2, 3),
    ]


def test_non_integer_gid_is_treated_as_empty():
    assert _is_empty_gid(None)
    assert _is_empty_gid("1")
    layer = TileLayer(name="odd", data=[[None, 1]])
    assert [r.gid for r in get_viewport_tiles(layer, (0, 0, 2, 1))] == [1]


def test_chunks_take_precedence_over_data():
    layer = TileLayer(
        name="both",
        chunks=[Chunk(coordinates=OrderedPair(0, 0), size=Size(1, 1), data=[[2]])],
        data=[[1]],
    )
    assert [r.gid for r in get_viewport_tiles(layer, (0, 0, 1, 1))] == [2]


def test_layer_id_and_name_copied_onto_result(tiled_map):
    results = get_viewport_tiles(tiled_map, TileRect(0, 0, 1, 1))
    assert results[0].layer_id == 2
    assert results[0].layer_name == "Finite"
    assert results[0].layer is tiled_map.layers[1]


def test_chunk_field_is_none_for_finite_layers(tiled_map):
    results = get_viewport_tiles(tiled_map, TileRect(0, 4, 6, 1))
    assert [r.gid for r in results] == [8, 9]
    assert all(result.chunk is None for result in results)


# ---------------------------------------------------------------------------
# Cost model: only the intersecting cells should be visited
# ---------------------------------------------------------------------------


class _CountingRows(list):
    """A list that records how many cells are read from it."""

    def __init__(self, values):
        super().__init__(values)
        self.reads = 0

    def __getitem__(self, index):
        self.reads += 1
        return super().__getitem__(index)


def test_small_viewport_does_not_scan_large_chunk():
    rows = [_CountingRows([1] * 64) for _ in range(64)]
    layer = TileLayer(
        name="big",
        chunks=[Chunk(coordinates=OrderedPair(0, 0), size=Size(64, 64), data=rows)],
    )

    results = get_viewport_tiles(layer, TileRect(10, 10, 2, 2))

    assert [(r.coordinates.x, r.coordinates.y, r.gid) for r in results] == [
        (10, 10, 1),
        (11, 10, 1),
        (10, 11, 1),
        (11, 11, 1),
    ]
    # Exactly 4 cells are inspected for a 2x2 viewport, even though the chunk
    # contains 4096 cells.
    assert sum(row.reads for row in rows) == 4


def test_non_intersecting_chunks_are_not_read():
    near = [_CountingRows([1])]
    far = [_CountingRows([2])]
    layer = TileLayer(
        name="two chunks",
        chunks=[
            Chunk(coordinates=OrderedPair(10000, 10000), size=Size(1, 1), data=far),
            Chunk(coordinates=OrderedPair(0, 0), size=Size(1, 1), data=near),
        ],
    )

    assert [r.gid for r in get_viewport_tiles(layer, TileRect(0, 0, 1, 1))] == [1]
    assert near[0].reads == 1
    assert far[0].reads == 0


def test_missing_chunks_are_not_scanned(monkeypatch, tiled_map):
    chunk_layer = tiled_map.layers[0]

    calls = 0
    original = _is_empty_gid

    def counting(gid):
        nonlocal calls
        calls += 1
        return original(gid)

    monkeypatch.setattr("pytiled_parser.viewport._is_empty_gid", counting)

    # The chunk at (-8, -4) is 8x4 but the viewport only reaches its first
    # column/row, so exactly one cell is inspected.
    get_viewport_tiles(chunk_layer, TileRect(-8, -3, 1, 1))
    assert calls == 1


def test_huge_viewport_still_only_reads_existing_cells():
    rows = [_CountingRows([1, 2]), _CountingRows([3, 4])]
    layer = TileLayer(name="finite", data=rows, coordinates=OrderedPair(-5, -5))

    get_viewport_tiles(layer, TileRect(-(10**9), -(10**9), 2 * 10**9, 2 * 10**9))
    assert sum(row.reads for row in rows) == 4
