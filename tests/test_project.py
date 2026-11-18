"""Tests for Tiled project (``.tiled-project``) custom class/enum support.

The fixtures under ``map_tests/project_classes`` are shared between the TMX and
JSON formats: both maps and both tilesets reference the same project file and
encode the same class assignments, defaults and overrides.
"""

import json
import os
from pathlib import Path

import pytest

from pytiled_parser import (
    CircularClassReferenceError,
    Color,
    InvalidEnumValueError,
    InvalidProjectError,
    InvalidPropertyPathError,
    ObjectRef,
    ProjectNotFoundError,
    PropertyTypeConflictError,
    UnknownClassError,
    parse_map,
    parse_project,
    parse_tileset,
)

TESTS_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
TEST_DATA = TESTS_DIR / "test_data"
MAP_TESTS = TEST_DATA / "map_tests"
PROJECT_FIXTURE = MAP_TESTS / "project_classes"
PROJECT_FILE = PROJECT_FIXTURE / "game.tiled-project"


def _all_objects(map_):
    """Yield every object in every object layer, including collision layers."""
    objects = []

    def walk_layer(layer):
        if getattr(layer, "tiled_objects", None) is not None:
            objects.extend(layer.tiled_objects)
        for child in getattr(layer, "layers", None) or []:
            walk_layer(child)

    for layer in map_.layers:
        walk_layer(layer)

    for tileset in map_.tilesets.values():
        for tile in (tileset.tiles or {}).values():
            if tile.objects is not None:
                objects.extend(tile.objects.tiled_objects)

    return objects


def _object_by_id(map_, object_id):
    for obj in _all_objects(map_):
        if obj.id == object_id:
            return obj
    raise KeyError(f"no object with id {object_id}")


def _tile(map_, tile_id):
    return next(iter(map_.tilesets.values())).tiles[tile_id]


@pytest.fixture(scope="module")
def project():
    return parse_project(PROJECT_FILE)


@pytest.fixture
def maps(project):
    tmx_map = parse_map(PROJECT_FIXTURE / "map.tmx", project=project)
    json_map = parse_map(PROJECT_FIXTURE / "map.json", project=project)
    return tmx_map, json_map


# ---------------------------------------------------------------------------
# Project loading
# ---------------------------------------------------------------------------


def test_parse_project_enums(project):
    mode = project.enums["MovementMode"]
    assert mode.storage_type == "string"
    assert mode.values == ["walk", "fly", "swim"]
    assert mode.values_as_flags is False

    flags = project.enums["Layers"]
    assert flags.values_as_flags is True


def test_parse_project_classes(project):
    character = project.classes["Character"]
    assert character.color == Color(204, 102, 0, 255)
    assert "object" in character.use_as

    # Member defaults are converted to the same typed representation the map
    # parsers use.
    stats_member = character.members["stats"]
    assert stats_member.type == "class"
    assert stats_member.property_type == "Stats"
    assert stats_member.default == {
        "maxHealth": 100,
        "moveSpeed": 5.5,
        "tint": Color(51, 102, 255, 255),
    }

    assert character.members["target"].default == ObjectRef(0)
    assert character.members["sprite"].default == Path("sprite.png")


def test_missing_project_file_raises(tmp_path):
    with pytest.raises(ProjectNotFoundError):
        parse_project(tmp_path / "nope.tiled-project")


def test_malformed_project_raises(tmp_path):
    bad = tmp_path / "bad.tiled-project"
    bad.write_text("{ not json", encoding="utf-8")
    with pytest.raises(InvalidProjectError):
        parse_project(bad)


def test_unknown_member_type_raises(tmp_path):
    bad = tmp_path / "bad.tiled-project"
    bad.write_text(
        json.dumps(
            {
                "propertyTypes": [
                    {
                        "type": "class",
                        "name": "Broken",
                        "members": [{"name": "x", "type": "mystery", "value": 1}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(InvalidProjectError):
        parse_project(bad)


def test_duplicate_type_name_raises(tmp_path):
    bad = tmp_path / "bad.tiled-project"
    bad.write_text(
        json.dumps(
            {
                "propertyTypes": [
                    {"type": "enum", "name": "Dup", "values": ["a"]},
                    {"type": "class", "name": "Dup", "members": []},
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(InvalidProjectError):
        parse_project(bad)


def test_legacy_project_types_key_is_accepted(tmp_path):
    legacy = tmp_path / "legacy.tiled-project"
    legacy.write_text(
        json.dumps(
            {
                "projectTypes": [
                    {
                        "type": "enum",
                        "name": "OldEnum",
                        "storageType": "string",
                        "values": ["one", "two"],
                        "valuesAsFlags": False,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    project = parse_project(legacy)
    assert project.enums["OldEnum"].values == ["one", "two"]


def test_int_enum_accepts_indices_and_flags(project):
    # A second project is built on the fly because the shared fixture only
    # declares string enums.
    project.enums["IntMode"] = type(project.enums["MovementMode"])(
        name="IntMode",
        storage_type="int",
        values=["a", "b", "c"],
        values_as_flags=False,
    )
    project.enums["IntMode"].validate(2, "test location")

    flags = type(project.enums["MovementMode"])(
        name="IntFlags",
        storage_type="int",
        values=["a", "b", "c"],
        values_as_flags=True,
    )
    # a | c == 1 | 4 == 5
    flags.validate(5, "test location")
    with pytest.raises(InvalidEnumValueError):
        flags.validate(8, "test location")
    with pytest.raises(PropertyTypeConflictError):
        flags.validate("a", "test location")


# ---------------------------------------------------------------------------
# Effective properties: defaults, overrides and nested classes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("index", [0, 1], ids=["tmx", "json"])
def test_map_class_defaults_and_override(maps, index):
    map_ = maps[index]
    # "difficulty" is overridden in the map, the other two come from defaults.
    assert map_.properties == {
        "timeOfDay": "day",
        "difficulty": 5,
        "ambient": Color(16, 32, 48, 255),
    }


@pytest.mark.parametrize("index", [0, 1], ids=["tmx", "json"])
def test_layer_class_defaults(maps, index):
    ground = maps[index].layers[0]
    assert ground.class_ == "Zone"
    assert ground.properties == {
        "zoneName": "default-zone",
        "enabled": True,
    }


@pytest.mark.parametrize("index", [0, 1], ids=["tmx", "json"])
def test_object_full_defaults(maps, index):
    # Object 2 sets no properties at all: every value comes from the class.
    obj = _object_by_id(maps[index], 2)
    assert obj.properties["health"] == 100
    assert obj.properties["moveSpeed"] == 7.25
    assert obj.properties["nameTag"] == "hero"
    assert obj.properties["hostile"] is False
    assert obj.properties["mode"] == "walk"
    assert obj.properties["layerMask"] == "ground,air"
    assert obj.properties["target"] == ObjectRef(0)
    assert obj.properties["sprite"] == Path("sprite.png")
    assert obj.properties["stats"] == {
        "maxHealth": 100,
        "moveSpeed": 5.5,
        "tint": Color(51, 102, 255, 255),
    }


@pytest.mark.parametrize("index", [0, 1], ids=["tmx", "json"])
def test_object_scalar_override_and_object_reference(maps, index):
    obj = _object_by_id(maps[index], 1)
    # Explicit health overrides the class default.
    assert obj.properties["health"] == 250
    # Untouched scalars keep their default values.
    assert obj.properties["moveSpeed"] == 7.25
    # Object references keep the referenced id.
    assert obj.properties["target"] == ObjectRef(2)


@pytest.mark.parametrize("index", [0, 1], ids=["tmx", "json"])
def test_nested_class_merged_field_by_field(maps, index):
    obj = _object_by_id(maps[index], 1)
    stats = obj.properties["stats"]
    # Explicit nested members override...
    assert stats["maxHealth"] == 500
    assert stats["tint"] == Color(255, 0, 0, 255)
    # ...while untouched nested members retain the nested class default.
    assert stats["moveSpeed"] == 5.5


@pytest.mark.parametrize("index", [0, 1], ids=["tmx", "json"])
def test_enum_override_and_bool_override(maps, index):
    obj = _object_by_id(maps[index], 3)
    assert obj.properties["mode"] == "fly"
    assert obj.properties["hostile"] is True
    # Default enum value is retained when not overridden.
    assert obj.properties["layerMask"] == "ground,air"


@pytest.mark.parametrize("index", [0, 1], ids=["tmx", "json"])
def test_tile_and_tileset_class_applied(maps, index):
    map_ = maps[index]
    tileset = next(iter(map_.tilesets.values()))

    assert tileset.properties == {"tilesetTag": "world-1"}

    # Tile 0 overrides "cost", "passable" comes from the TileMeta default.
    assert _tile(map_, 0).properties == {"cost": 5, "passable": True}
    # Tile 1 only has defaults.
    assert _tile(map_, 1).properties == {"cost": 1, "passable": True}


@pytest.mark.parametrize("index", [0, 1], ids=["tmx", "json"])
def test_collision_layer_object_class_applied(maps, index):
    collision_obj = _object_by_id(maps[index], 0)
    assert collision_obj.class_ == "Character"
    assert collision_obj.properties["health"] == 42
    # Nested defaults still flow into objects living in tile collision layers.
    assert collision_obj.properties["stats"]["moveSpeed"] == 5.5


@pytest.mark.parametrize("index", [0, 1], ids=["tmx", "json"])
def test_group_layer_class_and_nested_object_defaults(maps, index):
    map_ = maps[index]
    group = next(layer for layer in map_.layers if layer.name == "Zones")
    # The group itself carries the Zone class defaults.
    assert group.class_ == "Zone"
    assert group.properties == {
        "zoneName": "default-zone",
        "enabled": True,
    }

    # Objects nested inside the group's child layers still receive their
    # own class defaults.
    inner_object = _object_by_id(map_, 4)
    assert inner_object.class_ == "Character"
    assert inner_object.properties["moveSpeed"] == 7.25
    assert inner_object.properties["stats"]["maxHealth"] == 100


# ---------------------------------------------------------------------------
# TMX / JSON parity
# ---------------------------------------------------------------------------


def _normalized(value):
    """Normalize the pre-existing int/float formatting differences between the
    TMX and JSON parsers so only project-related values are compared."""
    if isinstance(value, dict):
        return {k: _normalized(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalized(v) for v in value]
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _properties_snapshot(map_):
    snapshot = {
        "map": map_.properties,
        "layers": [layer.properties for layer in map_.layers],
        "objects": {
            obj.id: _normalized(dict(obj.properties)) for obj in _all_objects(map_)
        },
        "tilesets": {
            tileset.name: {
                "properties": tileset.properties,
                "tiles": {
                    tile_id: tile.properties
                    for tile_id, tile in (tileset.tiles or {}).items()
                },
            }
            for tileset in map_.tilesets.values()
        },
    }
    return snapshot


def test_tmx_and_json_effective_properties_identical(maps):
    tmx_map, json_map = maps
    assert _properties_snapshot(tmx_map) == _properties_snapshot(json_map)


def test_auto_discovery_via_true():
    # The fixture directory holds exactly one .tiled-project file.
    map_ = parse_map(PROJECT_FIXTURE / "map.tmx", project=True)
    assert map_.properties["difficulty"] == 5


def test_explicit_project_path():
    map_ = parse_map(PROJECT_FIXTURE / "map.json", project=PROJECT_FILE)
    assert map_.properties["timeOfDay"] == "day"


def test_no_project_keeps_legacy_behavior():
    # Without a project the explicit map properties are returned untouched and
    # no class defaults are injected.
    map_ = parse_map(PROJECT_FIXTURE / "map.tmx")
    assert map_.properties == {"difficulty": 5}
    obj = _object_by_id(map_, 2)
    # No project means no defaults at all.
    assert obj.properties == {}


def test_auto_discovery_without_project_file_raises(tmp_path):
    map_file = tmp_path / "map.tmx"
    map_file.write_text(
        '<?xml version="1.0"?>'
        '<map version="1.9" tiledversion="1.9" orientation="orthogonal" '
        'renderorder="right-down" width="1" height="1" tilewidth="8" '
        'tileheight="8" infinite="0" nextlayerid="1" nextobjectid="1"/>',
        encoding="utf-8",
    )
    with pytest.raises(ProjectNotFoundError):
        parse_map(map_file, project=True)


def test_parse_tileset_with_project(project):
    tileset = parse_tileset(PROJECT_FIXTURE / "tileset.json", project=project)
    assert tileset.properties == {"tilesetTag": "world-1"}
    assert tileset.tiles[1].properties == {"cost": 1, "passable": True}


# ---------------------------------------------------------------------------
# Error conditions, all messages must identify the offending object/location
# ---------------------------------------------------------------------------


def _write_project(tmp_path, property_types):
    project_file = tmp_path / "custom.tiled-project"
    project_file.write_text(
        json.dumps({"propertyTypes": property_types}), encoding="utf-8"
    )
    return project_file


def test_unknown_class_error_mentions_object(tmp_path):
    _write_project(
        tmp_path,
        [
            {
                "type": "class",
                "name": "Real",
                "useAs": ["object"],
                "members": [{"name": "a", "type": "int", "value": 1}],
            }
        ],
    )
    map_file = tmp_path / "map.tmx"
    map_file.write_text(
        '<?xml version="1.0"?>'
        '<map version="1.9" tiledversion="1.9" orientation="orthogonal" '
        'renderorder="right-down" width="1" height="1" tilewidth="8" '
        'tileheight="8" infinite="0" nextlayerid="2" nextobjectid="2">'
        '<objectgroup id="1" name="Objects">'
        '<object id="1" class="Ghost" x="0" y="0"/>'
        "</objectgroup></map>",
        encoding="utf-8",
    )
    with pytest.raises(UnknownClassError) as exc_info:
        parse_map(map_file, project=True)
    message = str(exc_info.value)
    assert "Ghost" in message
    assert "object id=1" in message


def test_circular_class_reference_raises(tmp_path):
    project_file = _write_project(
        tmp_path,
        [
            {
                "type": "class",
                "name": "A",
                "useAs": ["property"],
                "members": [
                    {
                        "name": "b",
                        "type": "class",
                        "propertyType": "B",
                        "value": {},
                    }
                ],
            },
            {
                "type": "class",
                "name": "B",
                "useAs": ["property"],
                "members": [
                    {
                        "name": "a",
                        "type": "class",
                        "propertyType": "A",
                        "value": {},
                    }
                ],
            },
        ],
    )
    with pytest.raises(CircularClassReferenceError) as exc_info:
        parse_project(project_file)
    message = str(exc_info.value)
    assert "circular" in message.lower()
    assert "A" in message and "B" in message


def test_invalid_enum_value_raises_with_location(tmp_path):
    _write_project(
        tmp_path,
        [
            {
                "type": "enum",
                "name": "Mood",
                "storageType": "string",
                "values": ["happy", "sad"],
                "valuesAsFlags": False,
            },
            {
                "type": "class",
                "name": "Person",
                "useAs": ["object"],
                "members": [
                    {
                        "name": "mood",
                        "type": "enum",
                        "propertyType": "Mood",
                        "value": "happy",
                    }
                ],
            },
        ],
    )
    map_file = tmp_path / "map.json"
    map_file.write_text(
        json.dumps(
            {
                "width": 1,
                "height": 1,
                "tilewidth": 8,
                "tileheight": 8,
                "orientation": "orthogonal",
                "renderorder": "right-down",
                "infinite": False,
                "nextobjectid": 2,
                "nextlayerid": 2,
                "version": "1.9",
                "layers": [
                    {
                        "id": 1,
                        "name": "Objects",
                        "type": "objectgroup",
                        "opacity": 1,
                        "visible": True,
                        "draworder": "topdown",
                        "objects": [
                            {
                                "id": 1,
                                "class": "Person",
                                "x": 0,
                                "y": 0,
                                "width": 8,
                                "height": 8,
                                "rotation": 0,
                                "visible": True,
                                "name": "",
                                "properties": [
                                    {
                                        "name": "mood",
                                        "type": "enum",
                                        "propertytype": "Mood",
                                        "value": "angry",
                                    }
                                ],
                            }
                        ],
                    }
                ],
                "tilesets": [],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(InvalidEnumValueError) as exc_info:
        parse_map(map_file, project=True)
    message = str(exc_info.value)
    assert "angry" in message
    assert "Mood" in message
    assert "object id=1" in message


def test_type_conflict_error_mentions_property_and_object(tmp_path):
    _write_project(
        tmp_path,
        [
            {
                "type": "class",
                "name": "Thing",
                "useAs": ["object"],
                "members": [{"name": "count", "type": "int", "value": 1}],
            }
        ],
    )
    map_file = tmp_path / "map.json"
    map_file.write_text(
        json.dumps(
            {
                "width": 1,
                "height": 1,
                "tilewidth": 8,
                "tileheight": 8,
                "orientation": "orthogonal",
                "renderorder": "right-down",
                "infinite": False,
                "nextobjectid": 2,
                "nextlayerid": 2,
                "version": "1.9",
                "layers": [
                    {
                        "id": 1,
                        "name": "Objects",
                        "type": "objectgroup",
                        "opacity": 1,
                        "visible": True,
                        "draworder": "topdown",
                        "objects": [
                            {
                                "id": 1,
                                "class": "Thing",
                                "x": 0,
                                "y": 0,
                                "width": 8,
                                "height": 8,
                                "rotation": 0,
                                "visible": True,
                                "name": "",
                                "properties": [
                                    {
                                        "name": "count",
                                        "type": "int",
                                        "value": "not-an-int",
                                    }
                                ],
                            }
                        ],
                    }
                ],
                "tilesets": [],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(PropertyTypeConflictError) as exc_info:
        parse_map(map_file, project=True)
    message = str(exc_info.value)
    assert "count" in message
    assert "object id=1" in message


def test_bad_file_path_error_mentions_property_and_object(tmp_path):
    _write_project(
        tmp_path,
        [
            {
                "type": "class",
                "name": "Asset",
                "useAs": ["object"],
                "members": [
                    {
                        "name": "texture",
                        "type": "file",
                        "value": "missing.png",
                    }
                ],
            }
        ],
    )
    map_file = tmp_path / "map.tmx"
    map_file.write_text(
        '<?xml version="1.0"?>'
        '<map version="1.9" tiledversion="1.9" orientation="orthogonal" '
        'renderorder="right-down" width="1" height="1" tilewidth="8" '
        'tileheight="8" infinite="0" nextlayerid="2" nextobjectid="2">'
        '<objectgroup id="1" name="Objects">'
        '<object id="1" class="Asset" x="0" y="0"/>'
        "</objectgroup></map>",
        encoding="utf-8",
    )
    with pytest.raises(InvalidPropertyPathError) as exc_info:
        parse_map(map_file, project=True)
    message = str(exc_info.value)
    assert "missing.png" in message
    assert "texture" in message
    assert "object id=1" in message


def test_valid_file_default_resolves_relative_to_project(tmp_path):
    (tmp_path / "present.png").write_bytes(b"png")
    _write_project(
        tmp_path,
        [
            {
                "type": "class",
                "name": "Asset",
                "useAs": ["object"],
                "members": [
                    {
                        "name": "texture",
                        "type": "file",
                        "value": "present.png",
                    }
                ],
            }
        ],
    )
    map_file = tmp_path / "map.tmx"
    map_file.write_text(
        '<?xml version="1.0"?>'
        '<map version="1.9" tiledversion="1.9" orientation="orthogonal" '
        'renderorder="right-down" width="1" height="1" tilewidth="8" '
        'tileheight="8" infinite="0" nextlayerid="2" nextobjectid="2">'
        '<objectgroup id="1" name="Objects">'
        '<object id="1" class="Asset" x="0" y="0"/>'
        "</objectgroup></map>",
        encoding="utf-8",
    )
    map_ = parse_map(map_file, project=True)
    obj = _object_by_id(map_, 1)
    assert obj.properties["texture"] == Path("present.png")


def test_explicit_file_property_resolves_relative_to_map_dir(tmp_path):
    # The project lives at the root while the map lives in a subdirectory.
    # The explicit file value is authored next to the map, so existence is
    # checked against the map directory rather than the project directory.
    _write_project(
        tmp_path,
        [
            {
                "type": "class",
                "name": "Asset",
                "useAs": ["object"],
                "members": [
                    {
                        "name": "texture",
                        "type": "file",
                        "value": "default.png",
                    }
                ],
            }
        ],
    )
    map_dir = tmp_path / "maps"
    map_dir.mkdir()
    (map_dir / "explicit.png").write_bytes(b"png")

    map_file = map_dir / "map.tmx"
    map_file.write_text(
        '<?xml version="1.0"?>'
        '<map version="1.9" tiledversion="1.9" orientation="orthogonal" '
        'renderorder="right-down" width="1" height="1" tilewidth="8" '
        'tileheight="8" infinite="0" nextlayerid="2" nextobjectid="2">'
        '<objectgroup id="1" name="Objects">'
        '<object id="1" class="Asset" x="0" y="0">'
        "<properties>"
        '<property name="texture" type="file" value="explicit.png"/>'
        "</properties>"
        "</object>"
        "</objectgroup></map>",
        encoding="utf-8",
    )
    map_ = parse_map(map_file, project=tmp_path / "custom.tiled-project")
    obj = _object_by_id(map_, 1)
    assert obj.properties["texture"] == Path("explicit.png")


def test_multiple_project_files_is_ambiguous(tmp_path):
    (tmp_path / "a.tiled-project").write_text("{}", encoding="utf-8")
    (tmp_path / "b.tiled-project").write_text("{}", encoding="utf-8")
    map_file = tmp_path / "map.tmx"
    map_file.write_text(
        '<?xml version="1.0"?>'
        '<map version="1.9" tiledversion="1.9" orientation="orthogonal" '
        'renderorder="right-down" width="1" height="1" tilewidth="8" '
        'tileheight="8" infinite="0" nextlayerid="1" nextobjectid="1"/>',
        encoding="utf-8",
    )
    with pytest.raises(InvalidProjectError):
        parse_map(map_file, project=True)
