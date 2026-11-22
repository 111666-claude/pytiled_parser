"""Cross-format equivalence regression between the TMX and JSON parsers.

The fixtures in ``tests/test_data/equivalence/root`` are a single Tiled level
exported twice, once as ``map.tmx`` and once as ``map.json``.  Each export keeps
its own external tilesets/templates in the corresponding format (``.tsx``/``.tx``
next to the TMX, ``.json`` next to the JSON map), but both exports describe the
same content.

Two guarantees are checked:

1. Normalising the TMX map and normalising the JSON map yields the exact same
   format-neutral description (order, coordinates, GIDs, colors, property types).
2. That description also matches ``expected_normalized.json``, which is the
   hand-authored content spec for the level.  The spec is deliberately not
   produced from either parser, so agreement cannot come from copying one
   parser's internal representation onto the other.

The module also contains mutation tests: they inject a plausible format
deviation (object order, property type, tile GID) into one export and assert the
equivalence check fails, proving the regression is actually sensitive to those
divergences.  Finally, missing-dependency and malformed-file tests assert that
failures point at the exact file or byte position that is wrong.
"""

import json
import shutil
import xml.etree.ElementTree as etree
from pathlib import Path

import pytest

from pytiled_parser import UnknownFormat, parse_map

TESTS_DIR = Path(__file__).parent
EQUIVALENCE_DIR = TESTS_DIR / "test_data" / "equivalence"
GOLDEN_PATH = EQUIVALENCE_DIR / "expected_normalized.json"

TMX_MAP = "map.tmx"
JSON_MAP = "map.json"


# ---------------------------------------------------------------------------
# Format-neutral normalisation
#
# Everything below reads only the public parsed model and emits plain Python
# data.  Nothing branches on whether the source file was TMX or JSON.
# ---------------------------------------------------------------------------


def _num(value):
    """Round floats so exports with different decimal precision still agree."""
    if isinstance(value, float):
        return round(value, 4)
    return value


def _pair(value):
    return [_num(value[0]), _num(value[1])]


def _color(value):
    return None if value is None else list(value)


def _property_value(value):
    """Tag a property with its semantic type, then its value.

    Order matters: ``bool`` is a subclass of ``int``.
    """
    if isinstance(value, bool):
        return ["bool", value]
    if isinstance(value, int):
        return ["int", value]
    if isinstance(value, float):
        return ["float", _num(value)]
    if isinstance(value, Path):
        return ["path", value.as_posix()]
    if isinstance(value, str):
        return ["string", value]
    if isinstance(value, tuple) and len(value) == 4:
        return ["color", list(value)]
    raise AssertionError(f"Unhandled property value {value!r} ({type(value)!r})")


def _properties(value):
    if value is None:
        return []
    return [[name, *_property_value(val)] for name, val in sorted(value.items())]


def _image_path(image, map_dir):
    if image is None:
        return None
    image = Path(image)
    resolved = (map_dir / image).resolve()
    # Anchor paths at the level above the map directory so the same fixture
    # tree copied into a temporary directory normalizes identically.
    base = map_dir.resolve().parents[1]
    try:
        return resolved.relative_to(base).as_posix()
    except ValueError:  # pragma: no cover - defensive for external fixtures
        return resolved.name


def _object(obj, map_dir):
    kind = type(obj).__name__.lower()
    data = {
        "kind": kind,
        "id": obj.id,
        "name": obj.name,
        "class_": obj.class_,
        "position": _pair(obj.coordinates),
        "size": _pair(obj.size),
        "rotation": _num(obj.rotation),
        "visible": obj.visible,
        "properties": _properties(obj.properties),
    }

    if hasattr(obj, "gid"):
        data["gid"] = obj.gid
    if hasattr(obj, "points"):
        data["points"] = [_pair(point) for point in obj.points]
    if kind == "text":
        data["text"] = {
            "text": obj.text,
            "color": _color(obj.color),
            "font_family": obj.font_family,
            "font_size": _num(obj.font_size),
            "bold": obj.bold,
            "italic": obj.italic,
            "kerning": obj.kerning,
            "strike_out": obj.strike_out,
            "underline": obj.underline,
            "horizontal_align": obj.horizontal_align,
            "vertical_align": obj.vertical_align,
            "wrap": obj.wrap,
        }
    return data


def _layer(layer, map_dir):
    data = {
        "kind": type(layer).__name__.lower(),
        "name": layer.name,
        "id": layer.id,
        "opacity": _num(layer.opacity),
        "visible": layer.visible,
        "offset": _pair(layer.offset),
        "parallax": _pair(layer.parallax_factor),
        "class_": layer.class_,
        "tint_color": _color(layer.tint_color),
        "properties": _properties(layer.properties),
    }

    if layer.size is not None:
        data["size"] = _pair(layer.size)

    if hasattr(layer, "chunks") and layer.chunks is not None:
        data["chunks"] = [
            {
                "position": _pair(chunk.coordinates),
                "size": _pair(chunk.size),
                "data": [[_num(tile) for tile in row] for row in chunk.data],
            }
            for chunk in layer.chunks
        ]
    if hasattr(layer, "data") and layer.data is not None:
        data["data"] = [[_num(tile) for tile in row] for row in layer.data]

    if hasattr(layer, "draw_order"):
        data["draw_order"] = layer.draw_order
        data["objects"] = [_object(obj, map_dir) for obj in layer.tiled_objects]

    if hasattr(layer, "image"):
        data["image"] = _image_path(layer.image, map_dir)
        data["repeat_x"] = layer.repeat_x
        data["repeat_y"] = layer.repeat_y
        data["transparent_color"] = _color(layer.transparent_color)

    if hasattr(layer, "layers") and layer.layers is not None:
        data["layers"] = [_layer(child, map_dir) for child in layer.layers]

    return data


def _tileset(tileset, map_dir):
    data = {
        "firstgid": tileset.firstgid,
        "name": tileset.name,
        "class_": tileset.class_,
        "tile_size": [tileset.tile_width, tileset.tile_height],
        "tile_count": tileset.tile_count,
        "columns": tileset.columns,
        "spacing": tileset.spacing,
        "margin": tileset.margin,
        "image": _image_path(tileset.image, map_dir),
        "image_size": [_num(tileset.image_width), _num(tileset.image_height)],
        "properties": _properties(tileset.properties),
        "tiles": [],
    }

    for tile_id in sorted((tileset.tiles or {}).keys()):
        tile = tileset.tiles[tile_id]
        data["tiles"].append(
            {
                "id": tile.id,
                "class_": tile.class_,
                "properties": _properties(tile.properties),
                "animation": (
                    None
                    if tile.animation is None
                    else [
                        {"tile_id": frame.tile_id, "duration": frame.duration}
                        for frame in tile.animation
                    ]
                ),
            }
        )

    return data


def normalize_map(parsed_map):
    """Turn a parsed map into a format-neutral, order-preserving description."""
    map_file = Path(parsed_map.map_file)
    map_dir = map_file.parent

    return {
        "map": {
            "infinite": parsed_map.infinite,
            "map_size": _pair(parsed_map.map_size),
            "tile_size": _pair(parsed_map.tile_size),
            "orientation": parsed_map.orientation,
            "render_order": parsed_map.render_order,
            "background_color": _color(parsed_map.background_color),
            "class_": parsed_map.class_,
            "properties": _properties(parsed_map.properties),
            "next_layer_id": parsed_map.next_layer_id,
            "next_object_id": parsed_map.next_object_id,
        },
        "tilesets": [
            _tileset(parsed_map.tilesets[firstgid], map_dir)
            for firstgid in sorted(parsed_map.tilesets.keys())
        ],
        "layers": [_layer(layer, map_dir) for layer in parsed_map.layers],
    }


def load_golden():
    with open(GOLDEN_PATH) as golden_file:
        golden = json.load(golden_file)
    # The file carries a human-readable provenance note; it is not content.
    golden.pop("_comment", None)
    return golden


def assert_equivalent(tmx_path, json_path, golden=None):
    """Assert both exports normalise to the same content, and to the spec."""
    tmx_normalized = normalize_map(parse_map(Path(tmx_path)))
    json_normalized = normalize_map(parse_map(Path(json_path)))

    assert (
        tmx_normalized == json_normalized
    ), "TMX and JSON exports of the same level normalized differently"

    if golden is not None:
        assert tmx_normalized == golden, "TMX export does not match the content spec"
        assert json_normalized == golden, "JSON export does not match the content spec"

    return tmx_normalized


# ---------------------------------------------------------------------------
# Per-test fixture copy, so mutation and error tests can corrupt their own tree
# ---------------------------------------------------------------------------


@pytest.fixture
def equivalence_copy(tmp_path):
    """A throw-away copy of the whole equivalence fixture tree."""
    destination = tmp_path / "equivalence"
    shutil.copytree(EQUIVALENCE_DIR, destination)
    return destination / "root"


# ---------------------------------------------------------------------------
# The regression itself
# ---------------------------------------------------------------------------


def test_paired_fixtures_are_equivalent():
    """Content-identical TMX/JSON exports must normalize identically."""
    assert_equivalent(
        EQUIVALENCE_DIR / "root" / TMX_MAP, EQUIVALENCE_DIR / "root" / JSON_MAP
    )


def test_normalized_exports_match_content_spec():
    """Both exports must match the hand-authored, parser-independent spec."""
    golden = load_golden()
    normalized = assert_equivalent(
        EQUIVALENCE_DIR / "root" / TMX_MAP,
        EQUIVALENCE_DIR / "root" / JSON_MAP,
        golden=golden,
    )

    # Sanity-check that the spec covers each required dimension explicitly.
    assert normalized["layers"][1]["kind"] == "layergroup"
    assert [layer["kind"] for layer in normalized["layers"][1]["layers"]] == [
        "tilelayer",
        "objectlayer",
    ]
    object_layer = normalized["layers"][2]
    assert [obj["kind"] for obj in object_layer["objects"]] == [
        "rectangle",
        "ellipse",
        "point",
        "polygon",
        "polyline",
        "text",
        "tile",
        "rectangle",
        "rectangle",
        "tile",
    ]
    tile_gids = [obj["gid"] for obj in object_layer["objects"] if obj["kind"] == "tile"]
    assert tile_gids == [33, 51]
    assert normalized["layers"][3]["tint_color"] == [255, 0, 0, 255]
    assert normalized["layers"][5]["chunks"][1]["position"] == [4, 0]
    assert [ts["firstgid"] for ts in normalized["tilesets"]] == [1, 49, 51]


# ---------------------------------------------------------------------------
# Deliberate format deviations: the regression must fail on them
# ---------------------------------------------------------------------------


def _mutate_json(root, mutate):
    path = root / JSON_MAP
    with open(path) as json_file:
        raw = json.load(json_file)
    mutate(raw)
    with open(path, "w") as json_file:
        json.dump(raw, json_file)


def _object_layer(raw_map, name):
    for layer in raw_map["layers"]:
        if layer.get("name") == name:
            return layer
    raise AssertionError(f"layer {name!r} not found")


def test_injected_object_order_deviation_is_detected(equivalence_copy):
    """Swapping two objects in one export must break equivalence."""

    def mutate(raw):
        objects = _object_layer(raw, "Objects")["objects"]
        objects[0], objects[1] = objects[1], objects[0]

    _mutate_json(equivalence_copy, mutate)

    with pytest.raises(AssertionError, match="normalized differently"):
        assert_equivalent(
            equivalence_copy / TMX_MAP,
            equivalence_copy / JSON_MAP,
            golden=load_golden(),
        )


def test_injected_property_type_deviation_is_detected(equivalence_copy):
    """Changing an int property into a string in one export must break equivalence."""

    def mutate(raw):
        for prop in raw["properties"]:
            if prop["name"] == "int property":
                prop["type"] = "string"
                prop["value"] = "13"
                return
        raise AssertionError("int property not found")

    _mutate_json(equivalence_copy, mutate)

    with pytest.raises(AssertionError, match="normalized differently"):
        assert_equivalent(
            equivalence_copy / TMX_MAP,
            equivalence_copy / JSON_MAP,
            golden=load_golden(),
        )


def test_injected_gid_deviation_is_detected(equivalence_copy):
    """Changing a tile GID in one export must break equivalence."""

    def mutate(raw):
        chunks = _object_layer(raw, "Infinite Tiles")["chunks"]
        chunks[0]["data"][0] = 2

    _mutate_json(equivalence_copy, mutate)

    with pytest.raises(AssertionError, match="normalized differently"):
        assert_equivalent(
            equivalence_copy / TMX_MAP,
            equivalence_copy / JSON_MAP,
            golden=load_golden(),
        )


# ---------------------------------------------------------------------------
# Missing files: the error must locate the missing path
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("map_name", [TMX_MAP, JSON_MAP])
def test_missing_map_error_locates_file(equivalence_copy, map_name):
    missing_path = equivalence_copy / map_name
    missing_path.unlink()

    with pytest.raises(FileNotFoundError) as error:
        parse_map(missing_path)

    assert Path(error.value.filename).resolve() == missing_path.resolve()


@pytest.mark.parametrize(
    "map_name, missing",
    [
        (TMX_MAP, "shared/tiles.tsx"),
        (JSON_MAP, "shared/tiles.json"),
        (TMX_MAP, "root/template_prop_rect.tx"),
        (JSON_MAP, "root/template_prop_rect.json"),
    ],
)
def test_missing_dependency_error_locates_file(equivalence_copy, map_name, missing):
    missing_path = equivalence_copy.parent / missing
    missing_path.unlink()

    with pytest.raises(FileNotFoundError) as error:
        parse_map(equivalence_copy / map_name)

    # The traceback must name the exact file that went missing, not just "a file".
    assert Path(error.value.filename).name == missing_path.name
    assert Path(error.value.filename).resolve() == missing_path.resolve()


# ---------------------------------------------------------------------------
# Malformed input: the error must locate the position/offending content
# ---------------------------------------------------------------------------


def test_malformed_tmx_map_locates_position(equivalence_copy):
    bad_map = equivalence_copy / "bad_map.tmx"
    bad_map.write_text('<map width="8">\n')

    with pytest.raises(etree.ParseError) as error:
        parse_map(bad_map)

    assert "line" in str(error.value) and "column" in str(error.value)
    assert error.value.position == (2, 0)


def test_malformed_json_map_locates_position(equivalence_copy):
    bad_map = equivalence_copy / "bad_map.json"
    bad_map.write_text('{"width": ,')

    with pytest.raises(UnknownFormat) as error:
        parse_map(bad_map)

    cause = error.value.__context__
    assert isinstance(cause, json.JSONDecodeError)
    assert (cause.lineno, cause.colno) == (1, 11)


def test_malformed_tmx_external_tileset_locates_content(equivalence_copy):
    marker = "|||GARBAGE-TILESET|||"
    (equivalence_copy.parent / "shared" / "tiles.tsx").write_text(marker)

    with pytest.raises(json.JSONDecodeError) as error:
        parse_map(equivalence_copy / TMX_MAP)

    assert error.value.doc == marker
    assert error.value.pos == 0


def test_malformed_json_external_tileset_locates_content(equivalence_copy):
    marker = "|||GARBAGE-TILESET|||"
    (equivalence_copy.parent / "shared" / "tiles.json").write_text(marker)

    with pytest.raises(UnknownFormat) as error:
        parse_map(equivalence_copy / JSON_MAP)

    cause = error.value.__context__
    assert isinstance(cause, json.JSONDecodeError)
    assert cause.doc == marker
    assert cause.pos == 0


def test_malformed_tmx_template_locates_content(equivalence_copy):
    marker = "@@GARBAGE-TEMPLATE@@"
    (equivalence_copy / "template_prop_rect.tx").write_text(marker)

    with pytest.raises(json.JSONDecodeError) as error:
        parse_map(equivalence_copy / TMX_MAP)

    assert error.value.doc == marker
    assert error.value.pos == 0


def test_malformed_json_template_locates_content(equivalence_copy):
    marker = "@@GARBAGE-TEMPLATE@@"
    (equivalence_copy / "template_prop_rect.json").write_text(marker)

    with pytest.raises(UnknownFormat) as error:
        parse_map(equivalence_copy / JSON_MAP)

    cause = error.value.__context__
    assert isinstance(cause, json.JSONDecodeError)
    assert cause.doc == marker
    assert cause.pos == 0
