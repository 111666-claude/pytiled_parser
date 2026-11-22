"""Tests for parse budgets (resource limits) and malformed-map handling."""

import base64
import gzip
import json
import os
import resource
import time
import zlib
from pathlib import Path

import pytest

from pytiled_parser import (
    BudgetExceededError,
    MalformedMapError,
    ParseBudget,
    parse_map,
)

TESTS_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
TEST_DATA = TESTS_DIR / "test_data"


def make_b64_zlib(payload: bytes) -> str:
    return base64.b64encode(zlib.compress(payload)).decode("ascii")


def make_b64_gzip(payload: bytes) -> str:
    return base64.b64encode(gzip.compress(payload)).decode("ascii")


TMX_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<map version="1.0" tiledversion="1.9.0" orientation="orthogonal"
     renderorder="right-down" width="{map_w}" height="{map_h}"
     tilewidth="32" tileheight="32" infinite="0"
     nextlayerid="2" nextobjectid="1">
 {body}
</map>
"""


def tmx_tile_layer(name, width, height, data_element):
    return (
        f'<layer id="1" name="{name}" width="{width}" height="{height}">'
        f"{data_element}</layer>"
    )


def write_tmx(tmp_path: Path, body: str, map_w=4, map_h=4) -> Path:
    map_file = tmp_path / "map.tmx"
    map_file.write_text(TMX_TEMPLATE.format(map_w=map_w, map_h=map_h, body=body))
    return map_file


def json_tile_layer(name, width, height, **extra):
    layer = {
        "type": "tilelayer",
        "name": name,
        "width": width,
        "height": height,
        "opacity": 1,
        "visible": True,
    }
    layer.update(extra)
    return layer


def write_json(tmp_path: Path, layers) -> Path:
    map_file = tmp_path / "map.json"
    map_file.write_text(
        json.dumps(
            {
                "version": "1.0",
                "tiledversion": "1.9.0",
                "orientation": "orthogonal",
                "renderorder": "right-down",
                "width": 4,
                "height": 4,
                "tilewidth": 32,
                "tileheight": 32,
                "infinite": False,
                "nextobjectid": 1,
                "layers": layers,
                "tilesets": [],
            }
        )
    )
    return map_file


# ---------------------------------------------------------------------------
# Decompression bombs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("parser_type", ["json", "tmx"])
@pytest.mark.parametrize("packer", [make_b64_zlib, make_b64_gzip])
def test_compression_bomb_rejected(tmp_path, parser_type, packer):
    """A tiny compressed layer claiming to expand far past the budget."""
    compression = "zlib" if packer is make_b64_zlib else "gzip"
    # 64 MiB of zeroed tile data compresses down to a few KiB
    bomb = packer(b"\x00" * (64 * 1024 * 1024))

    if parser_type == "json":
        map_file = write_json(
            tmp_path,
            [
                json_tile_layer(
                    "bomb_layer",
                    128,
                    128,
                    encoding="base64",
                    compression=compression,
                    data=bomb,
                )
            ],
        )
    else:
        map_file = write_tmx(
            tmp_path,
            tmx_tile_layer(
                "bomb_layer",
                128,
                128,
                f'<data encoding="base64" compression="{compression}">{bomb}</data>',
            ),
        )

    budget = ParseBudget(max_decompressed_bytes=1024 * 1024)

    start = time.monotonic()
    rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    with pytest.raises(BudgetExceededError) as exc_info:
        parse_map(map_file, budget=budget)
    elapsed = time.monotonic() - start
    rss_after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    # The error carries the file and layer position
    assert exc_info.value.file == map_file
    assert exc_info.value.layer == "bomb_layer"
    assert "bomb_layer" in str(exc_info.value)
    assert str(map_file) in str(exc_info.value)
    assert exc_info.value.limit == 1024 * 1024

    # Rejected quickly, and the bomb never materialized in memory:
    # peak RSS grew by far less than the 64 MiB payload.
    assert elapsed < 2.0
    assert (rss_after - rss_before) < 32 * 1024  # ru_maxrss is in KiB on Linux


@pytest.mark.parametrize("parser_type", ["json", "tmx"])
def test_absurd_declared_dimensions_rejected_before_decoding(tmp_path, parser_type):
    """A map claiming hundreds of millions of tiles is rejected up front."""
    if parser_type == "json":
        map_file = write_json(
            tmp_path,
            [
                json_tile_layer(
                    "huge",
                    30_000,
                    30_000,
                    encoding="base64",
                    compression="",
                    data=make_b64_zlib(b"\x00" * 16),
                )
            ],
        )
    else:
        map_file = write_tmx(
            tmp_path,
            tmx_tile_layer(
                "huge",
                30_000,
                30_000,
                '<data encoding="base64">AAAAAA==</data>',
            ),
        )

    start = time.monotonic()
    with pytest.raises(BudgetExceededError) as exc_info:
        parse_map(map_file)
    assert time.monotonic() - start < 2.0
    assert exc_info.value.layer == "huge"
    assert exc_info.value.actual == 30_000 * 30_000


@pytest.mark.parametrize("parser_type", ["json", "tmx"])
def test_integer_overflow_dimensions_rejected(tmp_path, parser_type):
    """Dimensions near 2**63 must not overflow into an allocation attempt."""
    huge = 2**62
    if parser_type == "json":
        map_file = write_json(
            tmp_path,
            [json_tile_layer("overflow", huge, huge, data=[0, 0, 0, 0])],
        )
    else:
        map_file = write_tmx(
            tmp_path,
            tmx_tile_layer("overflow", huge, huge, "<data>0,0,0,0</data>"),
        )

    with pytest.raises(BudgetExceededError) as exc_info:
        parse_map(map_file)
    assert exc_info.value.actual == huge * huge


# ---------------------------------------------------------------------------
# Boundary maps: at the limit is legal, one past it is not
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("parser_type", ["json", "tmx"])
@pytest.mark.parametrize("width,height", [(4, 4), (5, 4)])
def test_tiles_per_layer_boundary(tmp_path, parser_type, width, height):
    """A map at exactly the tile limit parses; one tile over is rejected."""
    tile_count = width * height
    budget = ParseBudget(max_tiles_per_layer=16)

    if parser_type == "json":
        map_file = write_json(
            tmp_path,
            [json_tile_layer("boundary", width, height, data=[0] * tile_count)],
        )
    else:
        csv_data = ",".join(["0"] * tile_count)
        map_file = write_tmx(
            tmp_path,
            tmx_tile_layer(
                "boundary", width, height, f'<data encoding="csv">{csv_data}</data>'
            ),
        )

    if tile_count <= 16:
        map_ = parse_map(map_file, budget=budget)
        assert len(map_.layers) == 1
    else:
        with pytest.raises(BudgetExceededError) as exc_info:
            parse_map(map_file, budget=budget)
        assert exc_info.value.layer == "boundary"


@pytest.mark.parametrize("parser_type", ["json", "tmx"])
def test_decompressed_bytes_boundary(tmp_path, parser_type):
    """Decompressed output at exactly the limit parses; one byte over fails."""
    # 16 tiles = 64 bytes of raw tile data
    payload = b"\x00" * 64
    data = make_b64_zlib(payload)

    if parser_type == "json":
        layer = json_tile_layer(
            "exact", 4, 4, encoding="base64", compression="zlib", data=data
        )
        map_file = write_json(tmp_path, [layer])
    else:
        map_file = write_tmx(
            tmp_path,
            tmx_tile_layer(
                "exact",
                4,
                4,
                f'<data encoding="base64" compression="zlib">{data}</data>',
            ),
        )

    map_ = parse_map(map_file, budget=ParseBudget(max_decompressed_bytes=64))
    assert len(map_.layers) == 1

    with pytest.raises(BudgetExceededError):
        parse_map(map_file, budget=ParseBudget(max_decompressed_bytes=63))


# ---------------------------------------------------------------------------
# Layer / object counts and nesting depth
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("parser_type", ["json", "tmx"])
def test_layer_count_limit(tmp_path, parser_type):
    budget = ParseBudget(max_layers=2)
    if parser_type == "json":
        layers = [json_tile_layer(f"l{i}", 1, 1, data=[0]) for i in range(3)]
        map_file = write_json(tmp_path, layers)
    else:
        body = "".join(
            tmx_tile_layer(f"l{i}", 1, 1, '<data encoding="csv">0</data>')
            for i in range(3)
        )
        map_file = write_tmx(tmp_path, body)

    with pytest.raises(BudgetExceededError) as exc_info:
        parse_map(map_file, budget=budget)
    assert exc_info.value.layer == "l2"
    assert exc_info.value.limit == 2


@pytest.mark.parametrize("parser_type", ["json", "tmx"])
def test_object_count_limit(tmp_path, parser_type):
    budget = ParseBudget(max_objects=2)
    if parser_type == "json":
        objects = [
            {
                "id": i,
                "x": 0,
                "y": 0,
                "width": 1,
                "height": 1,
                "rotation": 0,
                "visible": True,
                "name": f"o{i}",
            }
            for i in range(3)
        ]
        layer = {
            "type": "objectgroup",
            "name": "objects",
            "draworder": "topdown",
            "opacity": 1,
            "visible": True,
            "objects": objects,
        }
        map_file = write_json(tmp_path, [layer])
    else:
        body = (
            '<objectgroup id="1" name="objects">'
            + "".join(
                f'<object id="{i}" name="o{i}" x="0" y="0" width="1" height="1"/>'
                for i in range(3)
            )
            + "</objectgroup>"
        )
        map_file = write_tmx(tmp_path, body)

    with pytest.raises(BudgetExceededError) as exc_info:
        parse_map(map_file, budget=budget)
    assert exc_info.value.layer == "objects"
    assert exc_info.value.actual == 3


def _nested_groups(depth, leaf):
    """Build nested JSON group layers, innermost last."""
    layer = leaf
    for i in reversed(range(depth)):
        layer = {
            "type": "group",
            "name": f"g{i}",
            "opacity": 1,
            "visible": True,
            "layers": [layer],
        }
    return layer


def test_group_nesting_depth_limit_json(tmp_path):
    leaf = json_tile_layer("leaf", 1, 1, data=[0])
    map_file = write_json(tmp_path, [_nested_groups(5, leaf)])

    # Depth 6 (5 groups + leaf) is fine with the default budget
    map_ = parse_map(map_file)
    assert len(map_.layers) == 1

    with pytest.raises(BudgetExceededError) as exc_info:
        parse_map(map_file, budget=ParseBudget(max_group_depth=3))
    # The layer position reflects the nesting path
    assert exc_info.value.layer == "g0/g1/g2/g3"


def test_group_nesting_depth_limit_tmx(tmp_path):
    body = '<layer id="9" name="leaf" width="1" height="1"><data encoding="csv">0</data></layer>'
    for i in reversed(range(5)):
        body = f'<group id="{i}" name="g{i}">{body}</group>'
    map_file = write_tmx(tmp_path, body)

    map_ = parse_map(map_file)
    assert len(map_.layers) == 1

    with pytest.raises(BudgetExceededError) as exc_info:
        parse_map(map_file, budget=ParseBudget(max_group_depth=3))
    assert exc_info.value.layer == "g0/g1/g2/g3"


# ---------------------------------------------------------------------------
# Infinite maps: chunks share the same counting semantics
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("parser_type", ["json", "tmx"])
def test_infinite_chunks_cumulative_tile_limit(tmp_path, parser_type):
    """Each chunk is under the limit, but the layer total is over it."""
    budget = ParseBudget(max_tiles_per_layer=32)
    chunk_tiles = [0] * 16  # 4x4 per chunk, 4 chunks = 64 tiles total

    if parser_type == "json":
        chunks = [
            {"x": i * 4, "y": 0, "width": 4, "height": 4, "data": chunk_tiles}
            for i in range(4)
        ]
        layer = json_tile_layer("infinite", 16, 4, chunks=chunks)
        map_file = write_json(tmp_path, [layer])
    else:
        chunk_xml = "".join(
            f'<chunk x="{i * 4}" y="0" width="4" height="4">'
            + ",".join(["0"] * 16)
            + "</chunk>"
            for i in range(4)
        )
        body = tmx_tile_layer(
            "infinite", 16, 4, f'<data encoding="csv">{chunk_xml}</data>'
        )
        map_file = write_tmx(tmp_path, body)

    with pytest.raises(BudgetExceededError) as exc_info:
        parse_map(map_file, budget=budget)
    assert exc_info.value.layer == "infinite"


@pytest.mark.parametrize("parser_type", ["json", "tmx"])
def test_infinite_chunk_compression_bomb(tmp_path, parser_type):
    """A compressed chunk in an infinite map cannot bypass the byte budget."""
    bomb = make_b64_zlib(b"\x00" * (8 * 1024 * 1024))
    budget = ParseBudget(max_decompressed_bytes=1024 * 1024)

    if parser_type == "json":
        chunks = [
            {"x": 0, "y": 0, "width": 16, "height": 16, "data": bomb},
        ]
        layer = json_tile_layer(
            "infinite", 16, 16, chunks=chunks, encoding="base64", compression="zlib"
        )
        map_file = write_json(tmp_path, [layer])
    else:
        chunk_xml = f'<chunk x="0" y="0" width="16" height="16">{bomb}</chunk>'
        body = tmx_tile_layer(
            "infinite",
            16,
            16,
            f'<data encoding="base64" compression="zlib">{chunk_xml}</data>',
        )
        map_file = write_tmx(tmp_path, body)

    with pytest.raises(BudgetExceededError) as exc_info:
        parse_map(map_file, budget=budget)
    assert exc_info.value.layer == "infinite"


def test_infinite_map_at_limit_passes(tmp_path):
    """A legal infinite map just under the budget parses fine (JSON + TMX)."""
    budget = ParseBudget(max_tiles_per_layer=64)
    chunk_tiles = [0] * 16

    chunks = [
        {"x": i * 4, "y": 0, "width": 4, "height": 4, "data": chunk_tiles}
        for i in range(4)
    ]
    json_file = write_json(tmp_path, [json_tile_layer("inf", 16, 4, chunks=chunks)])
    map_ = parse_map(json_file, budget=budget)
    assert len(map_.layers[0].chunks) == 4

    chunk_xml = "".join(
        f'<chunk x="{i * 4}" y="0" width="4" height="4">'
        + ",".join(["0"] * 16)
        + "</chunk>"
        for i in range(4)
    )
    tmx_file = write_tmx(
        tmp_path,
        tmx_tile_layer("inf", 16, 4, f'<data encoding="csv">{chunk_xml}</data>'),
    )
    map_ = parse_map(tmx_file, budget=budget)
    assert len(map_.layers[0].chunks) == 4


# ---------------------------------------------------------------------------
# Malformed maps: errors carry file and layer position
# ---------------------------------------------------------------------------


def test_tmx_missing_dimensions(tmp_path):
    body = '<layer id="1" name="nodims"><data encoding="csv">0,0,0,0</data></layer>'
    map_file = write_tmx(tmp_path, body)

    with pytest.raises(MalformedMapError) as exc_info:
        parse_map(map_file)
    assert exc_info.value.file == map_file
    assert exc_info.value.layer == "nodims"
    assert "nodims" in str(exc_info.value)


def test_json_missing_dimensions_with_data(tmp_path):
    layer = {
        "type": "tilelayer",
        "name": "nodims",
        "opacity": 1,
        "visible": True,
        "data": [0, 0, 0, 0],
    }
    map_file = write_json(tmp_path, [layer])

    with pytest.raises(MalformedMapError) as exc_info:
        parse_map(map_file)
    assert exc_info.value.file == map_file
    assert exc_info.value.layer == "nodims"


def test_json_chunk_missing_dimensions(tmp_path):
    layer = json_tile_layer(
        "infinite",
        16,
        16,
        chunks=[{"x": 0, "y": 0, "data": [0, 0, 0, 0]}],
    )
    map_file = write_json(tmp_path, [layer])

    with pytest.raises(MalformedMapError) as exc_info:
        parse_map(map_file)
    assert exc_info.value.layer == "infinite"


def test_tmx_negative_dimensions(tmp_path):
    body = tmx_tile_layer("negative", -5, 4, '<data encoding="csv">0</data>')
    map_file = write_tmx(tmp_path, body)

    with pytest.raises(MalformedMapError) as exc_info:
        parse_map(map_file)
    assert exc_info.value.layer == "negative"


def test_tmx_non_integer_dimensions(tmp_path):
    body = (
        '<layer id="1" name="notanint" width="lots" height="4">'
        '<data encoding="csv">0</data></layer>'
    )
    map_file = write_tmx(tmp_path, body)

    with pytest.raises(MalformedMapError) as exc_info:
        parse_map(map_file)
    assert exc_info.value.layer == "notanint"


# ---------------------------------------------------------------------------
# Input byte limit
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("parser_type", ["json", "tmx"])
def test_input_bytes_limit(tmp_path, parser_type):
    if parser_type == "json":
        map_file = write_json(tmp_path, [json_tile_layer("l", 1, 1, data=[0])])
    else:
        map_file = write_tmx(
            tmp_path, tmx_tile_layer("l", 1, 1, '<data encoding="csv">0</data>')
        )

    size = map_file.stat().st_size
    assert size > 10

    # At the limit: fine
    parse_map(map_file, budget=ParseBudget(max_input_bytes=size))

    with pytest.raises(BudgetExceededError) as exc_info:
        parse_map(map_file, budget=ParseBudget(max_input_bytes=size - 1))
    assert exc_info.value.file == map_file


# ---------------------------------------------------------------------------
# Budget validation and default compatibility
# ---------------------------------------------------------------------------


def test_budget_validation():
    with pytest.raises(ValueError):
        ParseBudget(max_layers=0)
    with pytest.raises(ValueError):
        ParseBudget(max_tiles_per_layer=-1)
    with pytest.raises(ValueError):
        ParseBudget(max_input_bytes="lots")


def test_default_budget_parses_shipped_fixtures():
    """The default budget must accept every normal map in the test data."""
    from pytiled_parser import UnknownFormat

    map_tests = TEST_DATA / "map_tests"
    parsed = 0
    for map_dir in map_tests.iterdir():
        for candidate in map_dir.iterdir():
            if candidate.suffix in (".tmx", ".json") and candidate.stem == "map":
                try:
                    parse_map(candidate)
                except UnknownFormat:
                    # intentionally-invalid fixtures (e.g. json_invalid_tileset)
                    continue
                parsed += 1
    assert parsed >= 5


def test_error_message_contains_file_and_layer(tmp_path):
    layer = json_tile_layer("context_layer", 100, 100, data=[0] * 10_001)
    map_file = write_json(tmp_path, [layer])

    with pytest.raises(BudgetExceededError) as exc_info:
        parse_map(map_file, budget=ParseBudget(max_tiles_per_layer=10_000))

    message = str(exc_info.value)
    assert str(map_file) in message
    assert "context_layer" in message
