"""Tests for the structured parser diagnostics.

These tests deliberately exercise *multiple* kinds of error in both formats:
syntax errors, wrong field types, bad layer/object/property values, unresolved
external references and template problems.  They assert the precision of the
diagnostics (file, location, layer/object, field path), that the concrete
exception types are still catchable through the historical public exceptions,
and that normal parsing is unaffected.
"""

import os
import xml.etree.ElementTree as etree
from pathlib import Path

import pytest

import pytiled_parser
from pytiled_parser import (
    CircularReferenceError,
    ExternalFileError,
    ExternalFileNotFoundError,
    FieldTypeError,
    FormatSyntaxError,
    InvalidFieldValueError,
    MissingFieldError,
    ParseError,
    TemplateError,
    UnknownFormat,
    UnknownLayerTypeError,
    UnrecognizedFormat,
    XMLSyntaxError,
    parse_map,
    parse_tileset,
    parse_world,
)

TESTS_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
DIAGNOSTICS = TESTS_DIR / "test_data" / "diagnostics"
JSON_TESTS = DIAGNOSTICS / "json"
TMX_TESTS = DIAGNOSTICS / "tmx"


def _error(path: Path) -> ParseError:
    with pytest.raises(ParseError) as caught:
        parse_map(path)
    return caught.value


# ---------------------------------------------------------------------------
# Syntax errors vs. genuinely unknown formats
# ---------------------------------------------------------------------------


def test_json_syntax_error_has_position():
    error = _error(JSON_TESTS / "syntax_error.json")

    assert isinstance(error, FormatSyntaxError)
    assert isinstance(error, ValueError)
    # Backwards compatibility: syntax errors were historically reported as
    # UnknownFormat by parse_map().
    assert isinstance(error, UnknownFormat)
    # Absolute paths are reduced to a bare file name (no directory leaks).
    assert error.file == "syntax_error.json"
    assert error.line == 4
    assert error.column is not None and error.column > 0
    assert error.offset is not None
    assert "trailing comma" in (error.reason or "")


def test_tmx_syntax_error_has_position():
    error = _error(TMX_TESTS / "syntax_error.tmx")

    assert isinstance(error, XMLSyntaxError)
    assert isinstance(error, etree.ParseError)
    assert isinstance(error, ValueError)
    assert isinstance(error, UnknownFormat)
    assert error.file == "syntax_error.tmx"
    assert error.line == 5
    assert error.column is not None


def test_genuinely_unknown_format_is_distinct(tmp_path):
    garbage = tmp_path / "garbage.garbage"
    garbage.write_text("|||not a map at all|||")

    with pytest.raises(UnknownFormat) as caught:
        parse_map(garbage)

    error = caught.value
    assert isinstance(error, UnrecognizedFormat)
    # A genuinely unknown format is NOT a ValueError: previously this was
    # conflated with invalid JSON.
    assert not isinstance(error, ValueError)
    assert not isinstance(error, FormatSyntaxError)
    assert error.file.endswith("garbage.garbage")


def test_empty_file_is_unrecognized(tmp_path):
    empty = tmp_path / "empty.json"
    empty.write_text("")

    with pytest.raises(UnrecognizedFormat):
        parse_map(empty)


def test_data_error_is_not_unknown_format():
    """Errors inside a recognized map must never masquerade as UnknownFormat."""

    error = _error(JSON_TESTS / "wrong_type_map_field.json")

    assert isinstance(error, FieldTypeError)
    assert not isinstance(error, UnknownFormat)


# ---------------------------------------------------------------------------
# Field types
# ---------------------------------------------------------------------------


def test_json_map_field_type_diagnostic():
    error = _error(JSON_TESTS / "wrong_type_map_field.json")

    assert isinstance(error, FieldTypeError)
    assert isinstance(error, TypeError)
    assert isinstance(error, ValueError)
    assert error.file.endswith("wrong_type_map_field.json")
    assert error.field_path == "height"
    assert error.line == 1
    assert error.column is not None


def test_tmx_map_attribute_type_diagnostic():
    error = _error(TMX_TESTS / "wrong_type_map_attr.tmx")

    assert isinstance(error, FieldTypeError)
    assert error.file.endswith("wrong_type_map_attr.tmx")
    assert error.field_path == "width"
    assert error.line > 0
    assert error.column is not None
    assert "eight" in error.message


def test_tmx_layer_attribute_type_diagnostic():
    error = _error(TMX_TESTS / "wrong_type_layer_attr.tmx")

    assert isinstance(error, FieldTypeError)
    assert error.layer == "'Tile Layer 1'"
    assert error.field_path == "layer.opacity"
    assert error.line > 0


def test_json_layer_property_type_diagnostic():
    error = _error(JSON_TESTS / "wrong_type_layer_property.json")

    assert isinstance(error, FieldTypeError)
    assert error.layer == "'Object Layer 1'"
    assert error.object == "id=2 name='crate'"
    assert error.field_path == "layers[0].objects[0].properties[0].value"
    assert error.line == 10


def test_tmx_layer_property_type_diagnostic():
    error = _error(TMX_TESTS / "wrong_type_property.tmx")

    assert isinstance(error, FieldTypeError)
    assert error.layer == "'Object Layer 1'"
    assert error.object == "id=1 name='crate'"
    assert error.field_path == "layer.properties['hp'].value"
    assert error.line == 6


def test_json_invalid_property_value():
    error = _error(JSON_TESTS / "wrong_property_type.json")

    assert isinstance(error, InvalidFieldValueError)
    assert isinstance(error, ValueError)
    assert error.field_path == "layers[0].objects[0].properties[0].value"
    assert "ultraviolet" in error.message


# ---------------------------------------------------------------------------
# Layer / object identification
# ---------------------------------------------------------------------------


def test_json_unknown_layer_type():
    error = _error(JSON_TESTS / "unknown_layer_type.json")

    assert isinstance(error, UnknownLayerTypeError)
    assert isinstance(error, RuntimeError)
    assert error.layer == "'Weird Layer'"
    assert error.field_path == "layers[0].type"
    assert "faketype" in error.message


def test_json_nested_group_layer_chain():
    error = _error(JSON_TESTS / "nested_group_bad_object.json")

    assert isinstance(error, FieldTypeError)
    assert error.layer == "'Outer' > 'Inner' > 'objs'"
    assert error.object == "id=9 name='rock'"
    assert error.field_path == "layers[0].layers[0].layers[0].objects[0].y"
    assert error.line == 7


def test_tmx_nested_group_layer_chain():
    error = _error(TMX_TESTS / "nested_group_bad_object.tmx")

    assert isinstance(error, FieldTypeError)
    assert error.layer == "'Outer' > 'Inner' > 'objs'"
    assert error.object == "id=9 name='rock'"
    assert error.field_path == "layer.y"
    assert error.line == 6


def test_tmx_invalid_csv_data_index():
    error = _error(TMX_TESTS / "invalid_csv_data.tmx")

    assert isinstance(error, InvalidFieldValueError)
    assert error.layer == "'Tile Layer 1'"
    assert error.field_path == "layer.data"
    assert "index 2" in error.message


def test_tmx_invalid_color():
    error = _error(TMX_TESTS / "invalid_color.tmx")

    assert isinstance(error, InvalidFieldValueError)
    assert error.field_path == "backgroundcolor"
    assert "#zzzzzz" in error.message


# ---------------------------------------------------------------------------
# External references
# ---------------------------------------------------------------------------


def test_json_missing_external_tileset():
    error = _error(JSON_TESTS / "missing_external_tileset.json")

    assert isinstance(error, ExternalFileNotFoundError)
    assert isinstance(error, FileNotFoundError)
    assert isinstance(error, OSError)
    assert error.field_path == "tilesets[0].source"
    assert "does_not_exist.json" in error.message


def test_tmx_missing_external_tileset():
    error = _error(TMX_TESTS / "missing_external_tileset.tmx")

    assert isinstance(error, ExternalFileNotFoundError)
    assert isinstance(error, OSError)
    assert error.field_path == "tileset.source"
    assert error.line == 3


def test_json_broken_external_tileset_points_at_tileset():
    error = _error(JSON_TESTS / "broken_external_tileset_map.json")

    assert isinstance(error, FieldTypeError)
    # External files are shown relative to the map which references them.
    assert error.file == "broken_external_tileset.json"
    assert error.field_path == "tileheight"
    assert error.line == 7


def test_external_content_error_names_referencing_field():
    """An error inside a referenced file still identifies where it came from."""

    error = _error(JSON_TESTS / "broken_external_tileset_map.json")

    assert error.referenced_from is not None
    assert "tilesets[0].source" in error.referenced_from
    # Without leaking the map's absolute directory.
    assert str(JSON_TESTS) not in error.referenced_from


def test_tmx_broken_external_tileset_points_at_tileset():
    error = _error(TMX_TESTS / "broken_external_tileset_map.tmx")

    assert isinstance(error, FieldTypeError)
    assert error.file == "broken_external_tileset.tsx"
    assert error.field_path == "tileset.tileheight"
    assert error.line == 2


# ---------------------------------------------------------------------------
# Templates: chains, nesting and cycles
# ---------------------------------------------------------------------------


def test_json_missing_template_reference():
    error = _error(JSON_TESTS / "missing_template.json")

    assert isinstance(error, ExternalFileNotFoundError)
    assert error.layer == "'objs'"
    assert error.object == "id=1"
    assert error.field_path == "layers[0].objects[0].template"


def test_tmx_missing_template_reference():
    error = _error(TMX_TESTS / "missing_template.tmx")

    assert isinstance(error, ExternalFileNotFoundError)
    assert error.layer == "'objs'"
    assert error.object == "id=1"
    assert error.field_path == "layer.template"


def test_json_circular_template_chain():
    error = _error(JSON_TESTS / "circular_template_map.json")

    assert isinstance(error, CircularReferenceError)
    assert isinstance(error, RuntimeError)
    assert error.file == "cycle_b.json"
    assert error.template_chain == "cycle_a.json -> cycle_b.json"
    assert "cycle_a.json -> cycle_b.json -> cycle_a.json" in error.message
    assert error.field_path == "object.template"


def test_tmx_circular_template_chain():
    error = _error(TMX_TESTS / "circular_template_map.tmx")

    assert isinstance(error, CircularReferenceError)
    assert error.file == "cycle_b.tx"
    assert error.template_chain == "cycle_a.tx -> cycle_b.tx"
    assert "cycle_a.tx -> cycle_b.tx -> cycle_a.tx" in error.message


def test_json_nested_template_error_points_at_innermost_file():
    error = _error(JSON_TESTS / "nested_template_bad_field_map.json")

    assert isinstance(error, FieldTypeError)
    assert error.file == "nested_base.json"
    assert error.field_path == "object.height"
    assert error.template_chain == "nested_child.json -> nested_base.json"
    assert error.layer == "'objs'"
    assert error.object == "id=1"


def test_tmx_nested_template_error_points_at_innermost_file():
    error = _error(TMX_TESTS / "nested_template_bad_field_map.tmx")

    assert isinstance(error, FieldTypeError)
    assert error.file == "nested_base.tx"
    assert error.field_path == "object.height"
    assert error.template_chain == "nested_child.tx -> nested_base.tx"
    assert error.layer == "'objs'"
    assert error.object == "id=1"


def test_multi_error_progression_narrows_locations(tmp_path):
    """With several errors in one map, fixing one reveals the next precisely.

    This mirrors the motivating scenario: a single wrongly-typed property made
    the importer fail with an opaque message, and the producer could not find
    where it was.  Each step here points at a progressively deeper location.
    """

    steps = []

    # 1. A wrong map-level type.
    bad_map = tmp_path / "map.json"
    bad_map.write_text(
        '{"layers":[],"nextlayerid":2,"nextobjectid":1,'
        '"orientation":"orthogonal","renderorder":"right-down",'
        '"tiledversion":"1.9.2","tileheight":"tall","tilesets":[],'
        '"tilewidth":32,"version":"1.9","width":8,"height":6}'
    )
    steps.append(_error(bad_map))

    # 2. Fix that; now a nested group layer has a bad object.
    bad_map.write_text(
        '{"layers":[{"type":"group","name":"Outer","id":3,"opacity":1,'
        '"visible":true,"layers":[{"type":"group","name":"Inner","id":4,'
        '"opacity":1,"visible":true,"layers":[{"type":"objectgroup",'
        '"name":"objs","id":5,"draworder":"topdown","opacity":1,'
        '"visible":true,"objects":[{"id":9,"name":"rock","rotation":0,'
        '"visible":true,"width":1,"height":1,"x":1,"y":"bad"}]}]}]}],'
        '"nextlayerid":6,"nextobjectid":10,"orientation":"orthogonal",'
        '"renderorder":"right-down","tiledversion":"1.9.2","tileheight":32,'
        '"tilesets":[],"tilewidth":32,"version":"1.9","width":8,"height":6}'
    )
    steps.append(_error(bad_map))

    # 3. Fix that; now a single object's property value is wrong.
    bad_map.write_text(
        '{"layers":[{"type":"objectgroup","name":"objs","id":2,'
        '"draworder":"topdown","opacity":1,"visible":true,"objects":'
        '[{"id":1,"name":"door","rotation":0,"visible":true,"width":1,'
        '"height":1,"x":1,"y":1,"properties":[{"name":"color",'
        '"type":"color","value":"ultraviolet"}]}]}],'
        '"nextlayerid":3,"nextobjectid":2,"orientation":"orthogonal",'
        '"renderorder":"right-down","tiledversion":"1.9.2","tileheight":32,'
        '"tilesets":[],"tilewidth":32,"version":"1.9","width":8,"height":6}'
    )
    steps.append(_error(bad_map))

    # Each diagnostic identifies a strictly deeper part of the document.
    assert steps[0].field_path == "tileheight"
    assert steps[0].layer is None
    assert steps[1].field_path.endswith(".objects[0].y")
    assert steps[1].layer == "'Outer' > 'Inner' > 'objs'"
    assert steps[1].object == "id=9 name='rock'"
    assert steps[2].field_path == "layers[0].objects[0].properties[0].value"
    assert steps[2].object == "id=1 name='door'"

    # Every one of them names the file and a line number.
    for error in steps:
        assert error.file is not None
        assert error.line is not None
        assert error.column is not None


def test_diagnostics_do_not_leak_absolute_paths(tmp_path):
    """References are displayed relative to the file that contains them."""

    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "base.json").write_text(
        '{"object": {"name":"base","rotation":0,"visible":true,'
        '"width":10,"height":"ten"},"type":"template"}'
    )
    maps = tmp_path / "maps"
    maps.mkdir()
    map_file = maps / "map.json"
    map_file.write_text(
        '{"layers":[{"type":"objectgroup","name":"objs","id":2,'
        '"draworder":"topdown","opacity":1,"visible":true,'
        '"objects":[{"id":1,"template":"../shared/base.json",'
        '"x":10,"y":20}]}],'
        '"nextlayerid":3,"nextobjectid":2,"orientation":"orthogonal",'
        '"renderorder":"right-down","tiledversion":"1.9.2",'
        '"tileheight":32,"tilesets":[],"tilewidth":32,"version":"1.9",'
        '"width":8,"height":6}'
    )

    with pytest.raises(FieldTypeError) as caught:
        parse_map(map_file)

    error = caught.value
    rendered = str(error) + str(error.to_dict())
    assert str(tmp_path) not in rendered
    assert "base.json" in error.file


# ---------------------------------------------------------------------------
# Backwards compatibility of the public API
# ---------------------------------------------------------------------------


def test_public_exceptions_are_exported():
    for name in (
        "ParseError",
        "UnknownFormat",
        "UnrecognizedFormat",
        "FormatSyntaxError",
        "MissingFieldError",
        "FieldTypeError",
        "ExternalFileNotFoundError",
        "UnknownLayerTypeError",
        "CircularReferenceError",
    ):
        assert name in pytiled_parser.__all__
        assert hasattr(pytiled_parser, name)


def test_historical_unknown_format_still_catchable():
    """The old ``except UnknownFormat`` idiom works for both syntax errors
    and genuinely unknown formats, but the subclasses remain distinguishable."""

    with pytest.raises(UnknownFormat) as syntax:
        parse_map(JSON_TESTS / "syntax_error.json")
    with pytest.raises(UnknownFormat) as unknown:
        parse_map(TESTS_DIR / "test_data" / "invalid_format.garbage")

    assert type(syntax.value) is not type(unknown.value)
    assert isinstance(syntax.value, FormatSyntaxError)
    assert isinstance(unknown.value, UnrecognizedFormat)


def test_error_reports_are_structured():
    error = _error(JSON_TESTS / "wrong_type_layer_property.json")

    data = error.to_dict()
    assert data["type"] == "FieldTypeError"
    assert data["file"].endswith("wrong_type_layer_property.json")
    assert data["field_path"] == "layers[0].objects[0].properties[0].value"
    assert data["layer"] == "'Object Layer 1'"
    assert data["object"] == "id=2 name='crate'"
    assert data["line"] == 10
    assert data["offset"] is not None

    rendered = str(error)
    assert "file:" in rendered
    assert "location:" in rendered
    assert "field:" in rendered


def test_diagnostics_are_repeatable():
    """Running the same failing parse twice gives identical diagnostics."""

    first = _error(JSON_TESTS / "nested_group_bad_object.json")
    second = _error(JSON_TESTS / "nested_group_bad_object.json")

    assert str(first) == str(second)
    assert first.to_dict() == second.to_dict()


def test_diagnostics_stack_is_cleaned_between_parses():
    """A failed parse must not contaminate a later successful parse."""

    _error(JSON_TESTS / "nested_group_bad_object.json")

    good = TESTS_DIR / "test_data" / "map_tests" / "no_layers" / "map.json"
    parsed = parse_map(good)
    assert parsed.map_size.width == 8

    # And a successful parse does not break the next failing one.
    error = _error(JSON_TESTS / "nested_group_bad_object.json")
    assert error.layer == "'Outer' > 'Inner' > 'objs'"


@pytest.mark.parametrize(
    "map_test",
    sorted(
        path
        for path in (TESTS_DIR / "test_data" / "map_tests").iterdir()
        # This fixture is intentionally an invalid tileset reference.
        if path.name != "json_invalid_tileset"
    ),
)
def test_normal_parsing_unaffected(map_test):
    """Every valid map still parses for both formats."""

    for name in ("map.json", "map.tmx"):
        candidate = map_test / name
        if candidate.exists():
            assert parse_map(candidate) is not None


@pytest.mark.parametrize(
    "tileset_dir",
    sorted((TESTS_DIR / "test_data" / "tilesets").iterdir()),
)
def test_normal_tileset_parsing_unaffected(tileset_dir):
    for name in ("tileset.json", "tileset.tsx"):
        candidate = tileset_dir / name
        if candidate.exists():
            assert parse_tileset(candidate) is not None


# ---------------------------------------------------------------------------
# Standalone tilesets and worlds
# ---------------------------------------------------------------------------


def test_standalone_tileset_type_error():
    error = _tileset_error(JSON_TESTS / "broken_external_tileset.json")

    assert isinstance(error, FieldTypeError)
    assert error.field_path == "tileheight"
    assert error.file.endswith("broken_external_tileset.json")
    assert error.line == 7


def test_standalone_tileset_syntax_error():
    error = _tileset_error(JSON_TESTS / "syntax_error.json")

    assert isinstance(error, FormatSyntaxError)
    assert error.line == 4


def test_standalone_tmx_tileset_syntax_error():
    error = _tileset_error(TMX_TESTS / "syntax_error.tmx")

    assert isinstance(error, XMLSyntaxError)
    assert error.line == 5


def test_world_map_type_error():
    world_file = DIAGNOSTICS / "world" / "wrong_type.world"
    with pytest.raises(FieldTypeError) as caught:
        parse_world(world_file)

    error = caught.value
    assert error.field_path == "maps[0].width"
    assert error.file.endswith("wrong_type.world")
    assert error.line == 3
    assert error.column is not None


def test_world_invalid_pattern_regexp():
    world_file = DIAGNOSTICS / "world" / "bad_regexp.world"
    with pytest.raises(FieldTypeError) as caught:
        parse_world(world_file)

    error = caught.value
    assert error.field_path == "patterns[0].regexp"
    assert "unterminated character set" in error.message


def _tileset_error(path: Path) -> ParseError:
    with pytest.raises(ParseError) as caught:
        parse_tileset(path)
    return caught.value


def test_external_file_error_base_class():
    assert issubclass(ExternalFileNotFoundError, ExternalFileError)
    assert issubclass(FormatSyntaxError, UnknownFormat)
    assert issubclass(ParseError, Exception)
    assert not issubclass(ParseError, UnknownFormat)
    assert issubclass(MissingFieldError, KeyError)
    assert issubclass(TemplateError, RuntimeError)
