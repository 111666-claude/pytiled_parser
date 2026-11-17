"""Tests for cross-format object templates.

A TMX map referencing JSON templates, and a JSON map referencing TMX
templates, must both merge template defaults into the object (with explicit
object values winning) and resolve the template's tileset into the host map.
"""

import json
from pathlib import Path

import pytest

from pytiled_parser import parse_map
from pytiled_parser.common_types import Color, OrderedPair, Size
from pytiled_parser.tiled_object import Rectangle, Tile

TEST_DIR = Path(__file__).parent.absolute()
IMAGES_DIR = TEST_DIR / ".." / "test_data" / "images"


def _check_map_properties(map_):
    # Custom properties and colors keep their normal parsed types
    assert map_.properties["bool property - true"] is True
    assert map_.properties["color property"] == Color(73, 252, 255, 255)
    assert isinstance(map_.properties["color property"], Color)
    assert map_.properties["file property"] == Path("../../../../../../var/log/syslog")
    assert isinstance(map_.properties["file property"], Path)
    assert map_.properties["float property"] == 1.23456789
    assert map_.properties["int property"] == 13
    assert isinstance(map_.properties["int property"], int)
    assert map_.properties["string property"] == "Hello, World!!"
    assert map_.background_color == Color(255, 0, 4, 255)


def _check_tilesets(map_):
    # The two tilesets referenced directly by the map, plus the one pulled
    # in by the tile object template, which is appended after the highest
    # existing firstgid.
    assert set(map_.tilesets.keys()) == {1, 49, 50}

    tile_set_image = map_.tilesets[1]
    assert tile_set_image.name == "tile_set_image"
    assert tile_set_image.tile_count == 48
    assert tile_set_image.image == (IMAGES_DIR / "tmw_desert_spacing.png").resolve()

    for_template = map_.tilesets[49]
    assert for_template.name == "tile_set_image_for_template"
    assert for_template.image == (IMAGES_DIR / "tile_04.png").resolve()

    template_tileset = map_.tilesets[50]
    assert template_tileset.name == "tile_set_single_image"
    assert template_tileset.tile_count == 1
    assert template_tileset.tiles[0].image == (IMAGES_DIR / "tile_02.png").resolve()


def _check_rect_properties(rect):
    # "test" is explicitly set on the object and overrides the template
    # default, "color prop" comes from the template and keeps its Color type
    assert rect.properties["test"] == "hello"
    assert rect.properties["color prop"] == Color(73, 252, 255, 255)
    assert isinstance(rect.properties["color prop"], Color)


def test_cross_template_tmx_json():
    """A TMX map referencing JSON object templates."""
    map_ = parse_map(TEST_DIR / "map.tmx")

    objects = map_.layers[0].tiled_objects

    rect = objects[0]
    assert isinstance(rect, Rectangle)
    assert rect.id == 2
    # Explicit values on the object itself
    assert rect.coordinates == OrderedPair(98.4988, 46.2385)
    # Defaults merged in from the JSON template
    assert rect.size == Size(63.6585878103079, 38.2811778048473)
    assert rect.rotation == 0
    assert rect.visible is True
    assert rect.name == ""
    assert rect.class_ == ""
    _check_rect_properties(rect)

    tile = objects[1]
    assert isinstance(tile, Tile)
    assert tile.id == 3
    assert tile.size == Size(32, 32)
    assert tile.coordinates == OrderedPair(46, 136.6667)
    # gid 1 within the template, re-based onto the template's tileset
    assert tile.gid == 50

    _check_tilesets(map_)
    _check_map_properties(map_)


def test_cross_template_json_tmx():
    """A JSON map referencing TMX object templates."""
    map_ = parse_map(TEST_DIR / "map.json")

    objects = map_.layers[0].tiled_objects

    rect = objects[0]
    assert isinstance(rect, Rectangle)
    assert rect.id == 2
    # Explicit values on the object itself
    assert rect.coordinates == OrderedPair(98.4987608686521, 46.2385012811358)
    # Defaults merged in from the TMX template
    assert rect.size == Size(63.6586, 38.2812)
    assert rect.rotation == 0
    assert rect.visible is True
    assert rect.name == ""
    assert rect.class_ == ""
    _check_rect_properties(rect)

    tile = objects[1]
    assert isinstance(tile, Tile)
    assert tile.id == 3
    assert tile.size == Size(32, 32)
    assert tile.coordinates == OrderedPair(46, 136.666666666667)
    # gid 1 within the template, re-based onto the template's tileset
    assert tile.gid == 50

    _check_tilesets(map_)
    _check_map_properties(map_)


TMX_MAP_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<map version="1.5" tiledversion="1.7.0" orientation="orthogonal" renderorder="right-down" width="1" height="1" tilewidth="32" tileheight="32" infinite="0" nextlayerid="2" nextobjectid="2">
{tilesets}
 <objectgroup id="1" name="Objects">
  <object id="1" template="{template}" x="0" y="0"/>
 </objectgroup>
</map>
"""


def _write_maps(directory, template_ref, tilesets=()):
    """Write a minimal TMX and JSON map referencing the given template."""
    tmx_tilesets = "\n".join(
        f' <tileset firstgid="{firstgid}" source="{source}"/>'
        for firstgid, source in tilesets
    )
    tmx_map = directory / "map.tmx"
    tmx_map.write_text(
        TMX_MAP_TEMPLATE.format(template=template_ref, tilesets=tmx_tilesets)
    )

    json_map = directory / "map.json"
    json_map.write_text(
        json.dumps(
            {
                "version": "1.5",
                "tiledversion": "1.7.0",
                "orientation": "orthogonal",
                "renderorder": "right-down",
                "width": 1,
                "height": 1,
                "tilewidth": 32,
                "tileheight": 32,
                "infinite": False,
                "nextlayerid": 2,
                "nextobjectid": 2,
                "type": "map",
                "tilesets": [
                    {"firstgid": firstgid, "source": source}
                    for firstgid, source in tilesets
                ],
                "layers": [
                    {
                        "draworder": "topdown",
                        "id": 1,
                        "name": "Objects",
                        "objects": [
                            {"id": 1, "template": template_ref, "x": 0, "y": 0}
                        ],
                        "opacity": 1,
                        "type": "objectgroup",
                        "visible": True,
                        "x": 0,
                        "y": 0,
                    }
                ],
            }
        )
    )

    return tmx_map, json_map


def test_nested_template_chain(tmp_path):
    """A template whose object references another template resolves fully."""
    (tmp_path / "template-inner.tx").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<template>\n"
        ' <object width="5" height="7">\n'
        "  <properties>\n"
        '   <property name="inner" value="yes"/>\n'
        "  </properties>\n"
        " </object>\n"
        "</template>\n"
    )
    (tmp_path / "template-outer.tx").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<template>\n"
        ' <object template="template-inner.tx" name="outer"/>\n'
        "</template>\n"
    )
    tmx_map, json_map = _write_maps(tmp_path, "template-outer.tx")

    for map_file in (tmx_map, json_map):
        map_ = parse_map(map_file)
        obj = map_.layers[0].tiled_objects[0]
        assert obj.size == Size(5, 7)
        assert obj.name == "outer"
        assert obj.properties == {"inner": "yes"}


def test_missing_template_file(tmp_path):
    tmx_map, json_map = _write_maps(tmp_path, "missing-template.tx")

    with pytest.raises(FileNotFoundError, match="missing-template.tx"):
        parse_map(tmx_map)

    with pytest.raises(FileNotFoundError, match="missing-template.tx"):
        parse_map(json_map)


def test_template_path_is_not_a_file(tmp_path):
    (tmp_path / "template-dir.tx").mkdir()
    tmx_map, json_map = _write_maps(tmp_path, "template-dir.tx")

    with pytest.raises(FileNotFoundError, match="template-dir.tx"):
        parse_map(tmx_map)

    with pytest.raises(FileNotFoundError, match="template-dir.tx"):
        parse_map(json_map)


def test_circular_template_reference(tmp_path):
    (tmp_path / "template-a.tx").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<template><object template="template-b.tx" width="5" height="5"/></template>\n'
    )
    (tmp_path / "template-b.tx").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<template><object template="template-a.tx" width="5" height="5"/></template>\n'
    )
    tmx_map, json_map = _write_maps(tmp_path, "template-a.tx")

    with pytest.raises(ValueError, match="[Cc]ircular"):
        parse_map(tmx_map)

    with pytest.raises(ValueError, match="[Cc]ircular"):
        parse_map(json_map)


def test_malformed_template(tmp_path):
    (tmp_path / "broken.tx").write_text("<template><object")
    (tmp_path / "broken.json").write_text("{ this is not json")

    tmx_map, json_map = _write_maps(tmp_path, "broken.tx")
    with pytest.raises(ValueError, match="broken.tx"):
        parse_map(tmx_map)
    with pytest.raises(ValueError, match="broken.tx"):
        parse_map(json_map)

    tmx_map, json_map = _write_maps(tmp_path, "broken.json")
    with pytest.raises(ValueError, match="broken.json"):
        parse_map(tmx_map)
    with pytest.raises(ValueError, match="broken.json"):
        parse_map(json_map)


def test_template_without_object(tmp_path):
    (tmp_path / "no-object.tx").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<template>\n</template>\n'
    )
    (tmp_path / "no-object.json").write_text('{"type": "template"}')

    tmx_map, json_map = _write_maps(tmp_path, "no-object.tx")
    with pytest.raises(ValueError, match="no-object.tx"):
        parse_map(tmx_map)
    with pytest.raises(ValueError, match="no-object.tx"):
        parse_map(json_map)

    tmx_map, json_map = _write_maps(tmp_path, "no-object.json")
    with pytest.raises(ValueError, match="no-object.json"):
        parse_map(tmx_map)
    with pytest.raises(ValueError, match="no-object.json"):
        parse_map(json_map)


def test_template_with_missing_tileset(tmp_path):
    (tmp_path / "template-tile.tx").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<template>\n"
        ' <tileset firstgid="1" source="missing-tileset.tsx"/>\n'
        ' <object gid="1" width="32" height="32"/>\n'
        "</template>\n"
    )
    tmx_map, json_map = _write_maps(tmp_path, "template-tile.tx")

    with pytest.raises(FileNotFoundError, match="missing-tileset.tsx"):
        parse_map(tmx_map)

    with pytest.raises(FileNotFoundError, match="missing-tileset.tsx"):
        parse_map(json_map)


def test_conflicting_tileset(tmp_path):
    # The map loads a tileset named "conflict_ts", and the template pulls in
    # a different tileset that happens to have the same name.
    (tmp_path / "tileset-a.tsx").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<tileset version="1.5" tiledversion="1.7.0" name="conflict_ts" '
        'tilewidth="32" tileheight="32" tilecount="1" columns="1">\n'
        ' <image source="a.png" width="32" height="32"/>\n'
        "</tileset>\n"
    )
    (tmp_path / "tileset-b.tsx").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<tileset version="1.5" tiledversion="1.7.0" name="conflict_ts" '
        'tilewidth="32" tileheight="32" tilecount="2" columns="2">\n'
        ' <image source="b.png" width="64" height="32"/>\n'
        "</tileset>\n"
    )
    (tmp_path / "template-tile.tx").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<template>\n"
        ' <tileset firstgid="1" source="tileset-b.tsx"/>\n'
        ' <object gid="1" width="32" height="32"/>\n'
        "</template>\n"
    )
    tmx_map, json_map = _write_maps(
        tmp_path, "template-tile.tx", tilesets=[(1, "tileset-a.tsx")]
    )

    with pytest.raises(ValueError, match="conflict_ts"):
        parse_map(tmx_map)

    with pytest.raises(ValueError, match="conflict_ts"):
        parse_map(json_map)
