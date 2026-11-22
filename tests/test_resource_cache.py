"""Tests for the per-call external resource cache.

Verifies that within a single parse_map call, external templates and
tilesets referenced by many objects are only opened/parsed once, that
per-object results stay independent, that errors keep their context, and
that no state leaks between separate parse_map calls.
"""

import builtins
import time
import xml.etree.ElementTree as etree
from contextlib import contextmanager
from pathlib import Path

import pytest

import pytiled_parser.parser
import pytiled_parser.util as util
from pytiled_parser import parse_map

TILESET_TSX = """<?xml version="1.0" encoding="UTF-8"?>
<tileset version="1.9" tiledversion="1.9.2" name="test_tileset" tilewidth="32" tileheight="32" tilecount="4" columns="2">
 <image source="tiles.png" width="64" height="64"/>
</tileset>
"""

TEMPLATE_TX = """<?xml version="1.0" encoding="UTF-8"?>
<template>
 <tileset firstgid="1" source="tileset.tsx"/>
 <object gid="1" width="32" height="32">
  <properties>
   <property name="hp" type="int" value="10"/>
  </properties>
 </object>
</template>
"""

MAP_HEADER = """<?xml version="1.0" encoding="UTF-8"?>
<map version="1.9" tiledversion="1.9.2" orientation="orthogonal" renderorder="right-down" width="8" height="6" tilewidth="32" tileheight="32" infinite="0" nextlayerid="2" nextobjectid="{next_id}">
 <tileset firstgid="1" source="tileset.tsx"/>
 <tileset firstgid="100" source="tileset.tsx"/>
 <objectgroup id="2" name="objects">
"""

MAP_FOOTER = """ </objectgroup>
</map>
"""


def build_template_map(directory: Path, object_count: int) -> Path:
    """Create a map fixture with many objects sharing one external template
    and one external tileset (referenced twice under different firstgids)."""
    (directory / "tileset.tsx").write_text(TILESET_TSX, encoding="utf-8")
    (directory / "template.tx").write_text(TEMPLATE_TX, encoding="utf-8")

    objects = []
    for i in range(object_count):
        if i == 0:
            # One object overrides the template property.
            objects.append(
                f'  <object id="{i + 1}" template="template.tx" x="{i}" y="0">'
                f'  <properties><property name="hp" type="int" value="99"/></properties>'
                f"  </object>"
            )
        else:
            objects.append(
                f'  <object id="{i + 1}" template="template.tx" x="{i}" y="0"/>'
            )

    map_file = directory / "map.tmx"
    map_file.write_text(
        MAP_HEADER.format(next_id=object_count + 1) + "\n".join(objects) + MAP_FOOTER,
        encoding="utf-8",
    )
    return map_file


class OpenCounter:
    """A counting filesystem shim: wraps builtins.open and records how many
    times each path is opened."""

    def __init__(self, monkeypatch):
        self.counts = {}
        real_open = builtins.open

        def counting_open(file, *args, **kwargs):
            key = str(Path(file).resolve()) if not isinstance(file, int) else file
            self.counts[key] = self.counts.get(key, 0) + 1
            return real_open(file, *args, **kwargs)

        monkeypatch.setattr(builtins, "open", counting_open)

    def opens_for(self, path: Path) -> int:
        return self.counts.get(str(path.resolve()), 0)


def get_objects(map_):
    return [
        obj
        for layer in map_.layers
        if hasattr(layer, "tiled_objects")
        for obj in layer.tiled_objects
    ]


def test_shared_template_and_tileset_opened_once(tmp_path, monkeypatch):
    map_file = build_template_map(tmp_path, 500)
    counter = OpenCounter(monkeypatch)

    map_ = parse_map(map_file)

    assert len(get_objects(map_)) == 500
    # Each external resource is opened exactly once for the whole map,
    # no matter how many objects reference it.
    assert counter.opens_for(tmp_path / "template.tx") == 1
    assert counter.opens_for(tmp_path / "tileset.tsx") == 1


def test_cache_key_distinguishes_encoding(tmp_path, monkeypatch):
    resource = tmp_path / "template.tx"
    resource.write_text(TEMPLATE_TX, encoding="utf-8")
    counter = OpenCounter(monkeypatch)

    with util.resource_cache_scope():
        util.load_raw_resource(resource, "utf-8")
        util.load_raw_resource(resource, "utf-8")
        assert counter.opens_for(resource) == 1
        # A different encoding is a different cache key.
        util.load_raw_resource(resource, "latin-1")
        assert counter.opens_for(resource) == 2


def test_objects_from_shared_template_are_independent(tmp_path):
    map_file = build_template_map(tmp_path, 10)
    map_ = parse_map(map_file)
    objects = get_objects(map_)

    # Distinct instances per object.
    assert len({id(obj) for obj in objects}) == 10

    # Per-object override and template default both respected.
    assert objects[0].properties["hp"] == 99
    assert objects[1].properties["hp"] == 10

    # Mutating one object must not leak into any other object.
    objects[1].properties["hp"] = 12345
    objects[1].name = "mutated"
    assert objects[2].properties["hp"] == 10
    assert objects[2].name != "mutated"

    # gid was resolved against the host map's tileset (firstgid 1), and each
    # object kept its own coordinates.
    assert all(obj.gid == 1 for obj in objects)
    assert [obj.coordinates[0] for obj in objects] == [float(i) for i in range(10)]


def test_shared_tileset_firstgid_computed_per_reference(tmp_path):
    map_file = build_template_map(tmp_path, 5)
    map_ = parse_map(map_file)

    # The same tileset file referenced under two firstgids yields two
    # independent Tileset objects with their own firstgid.
    assert set(map_.tilesets.keys()) == {1, 100}
    assert map_.tilesets[1] is not map_.tilesets[100]
    assert map_.tilesets[1].firstgid == 1
    assert map_.tilesets[100].firstgid == 100
    # Relative image path resolved against the tileset's own directory.
    assert map_.tilesets[1].image == (tmp_path / "tiles.png").resolve()
    assert map_.tilesets[100].image == (tmp_path / "tiles.png").resolve()


def test_no_stale_state_between_parse_calls(tmp_path):
    map_file = build_template_map(tmp_path, 3)

    map_ = parse_map(map_file)
    assert get_objects(map_)[0].size[0] == 32

    # Change the template on disk; a new parse_map call must see it.
    (tmp_path / "template.tx").write_text(
        TEMPLATE_TX.replace('width="32"', 'width="64"'), encoding="utf-8"
    )
    map_ = parse_map(map_file)
    assert get_objects(map_)[0].size[0] == 64


def test_file_changed_mid_parse_stays_consistent(tmp_path, monkeypatch):
    map_file = build_template_map(tmp_path, 50)

    real_read = util._read_raw_resource

    def rewriting_read(file_path, encoding):
        result = real_read(file_path, encoding)
        if Path(file_path).name == "template.tx":
            # Simulate another process changing the template right after it
            # was read, while the rest of the map is still being parsed.
            (tmp_path / "template.tx").write_text(
                TEMPLATE_TX.replace('width="32"', 'width="64"'), encoding="utf-8"
            )
        return result

    monkeypatch.setattr(util, "_read_raw_resource", rewriting_read)
    map_ = parse_map(map_file)

    # Every object consistently reflects the first read of the template.
    assert all(obj.size[0] == 32 for obj in get_objects(map_))


def test_missing_template_error_has_context(tmp_path):
    map_file = build_template_map(tmp_path, 2)
    (tmp_path / "template.tx").unlink()

    with pytest.raises(FileNotFoundError) as exc_info:
        parse_map(map_file)
    assert "template.tx" in str(exc_info.value)


def test_corrupt_template_error_has_context(tmp_path):
    map_file = build_template_map(tmp_path, 2)
    (tmp_path / "template.tx").write_text("<template><object", encoding="utf-8")

    with pytest.raises(etree.ParseError) as exc_info:
        parse_map(map_file)
    assert "template.tx" in str(exc_info.value)


def test_circular_reference_detected(tmp_path, monkeypatch):
    resource = tmp_path / "loop.tx"
    resource.write_text(TEMPLATE_TX, encoding="utf-8")

    real_read = util._read_raw_resource

    def reentrant_read(file_path, encoding):
        # Simulate a resource that, while being parsed, needs itself again.
        util.load_raw_resource(file_path, encoding)
        return real_read(file_path, encoding)

    monkeypatch.setattr(util, "_read_raw_resource", reentrant_read)

    with util.resource_cache_scope():
        with pytest.raises(RuntimeError, match="Circular reference"):
            util.load_raw_resource(resource, "utf-8")

    # The failed load is not cached; the resource can be loaded afterwards.
    monkeypatch.setattr(util, "_read_raw_resource", real_read)
    with util.resource_cache_scope():
        assert util.load_raw_resource(resource, "utf-8") is not None


def test_cached_parse_is_faster(tmp_path, monkeypatch):
    map_file = build_template_map(tmp_path, 2000)

    # Baseline: disable the cache scope so every reference re-reads the files.
    @contextmanager
    def no_scope():
        yield

    monkeypatch.setattr(pytiled_parser.parser, "resource_cache_scope", no_scope)
    start = time.perf_counter()
    parse_map(map_file)
    uncached_time = time.perf_counter() - start
    monkeypatch.undo()

    counter = OpenCounter(monkeypatch)
    start = time.perf_counter()
    map_ = parse_map(map_file)
    cached_time = time.perf_counter() - start

    assert len(get_objects(map_)) == 2000
    assert counter.opens_for(tmp_path / "template.tx") == 1
    assert counter.opens_for(tmp_path / "tileset.tsx") == 1
    print(
        f"\nuncached: {uncached_time:.3f}s, cached: {cached_time:.3f}s "
        f"({uncached_time / max(cached_time, 1e-9):.1f}x speedup)"
    )
    assert cached_time < uncached_time
