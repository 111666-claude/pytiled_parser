"""Tests for resolution of `object` type custom properties."""

import os
from pathlib import Path

import pytest

from pytiled_parser import (
    ObjectRef,
    ObjectTemplateError,
    parse_map,
    resolve_object_references,
)
from pytiled_parser.layer import LayerGroup
from pytiled_parser.tiled_object import Ellipse, Point, Rectangle

TESTS_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
OBJECT_REF_TESTS = TESTS_DIR / "test_data" / "object_ref_tests"
BROKEN_TEMPLATE_TESTS = OBJECT_REF_TESTS / "broken_template"


def _parse(fmt):
    return parse_map(OBJECT_REF_TESTS / fmt / f"map.{fmt}")


@pytest.fixture(params=["json", "tmx"])
def tiled_map(request):
    return _parse(request.param)


@pytest.fixture()
def resolution(tiled_map):
    return resolve_object_references(tiled_map)


def test_map_level_property(tiled_map, resolution):
    # map property `spawn` -> object 4 in layer_b
    target = resolution.resolved["properties['spawn']"]
    assert target is tiled_map.layers[1].tiled_objects[0]
    assert isinstance(target, Point)
    assert target.name == "spawn"


def test_layer_level_property(tiled_map, resolution):
    # layer_a property `entry` -> object 1 in layer_a
    target = resolution.resolved["layers[0].properties['entry']"]
    assert target is tiled_map.layers[0].tiled_objects[0]


def test_cross_layer_object_property(tiled_map, resolution):
    # object 1 in layer_a points at object 4 in layer_b
    target = resolution.resolved["layers[0].tiled_objects[0].properties['target']"]
    assert target is tiled_map.layers[1].tiled_objects[0]


def test_object_property_same_layer(tiled_map, resolution):
    # object 2 (ellipse) points at object 1 in the same layer
    target = resolution.resolved["layers[0].tiled_objects[1].properties['target']"]
    assert target is tiled_map.layers[0].tiled_objects[0]
    assert isinstance(target, Rectangle)


def test_group_nested_layer(tiled_map, resolution):
    # object 10 lives in an object layer nested inside a group layer
    group = tiled_map.layers[2]
    assert isinstance(group, LayerGroup)
    nested = group.layers[0].tiled_objects[0]

    assert resolution.resolved["properties['nested']"] is nested
    # ...and references out of the nested layer resolve across the map
    target = resolution.resolved[
        "layers[2].layers[0].tiled_objects[1].properties['target']"
    ]
    assert target is tiled_map.layers[0].tiled_objects[0]


def test_template_instance(tiled_map, resolution):
    # object 3 is a template instance; it keeps its own id, its overridden
    # `target` property, and inherits `label` from the template
    instance = tiled_map.layers[0].tiled_objects[2]
    assert instance.id == 3
    assert instance.name == "templated"
    assert instance.properties["label"] == "from-template"

    # the overridden target (2) wins over the template's target (1)
    target = resolution.resolved["layers[0].tiled_objects[2].properties['target']"]
    assert target is tiled_map.layers[0].tiled_objects[1]
    assert isinstance(target, Ellipse)

    # references *to* a template instance resolve to the instance itself
    from_tile = resolution.resolved["tilesets[1].tiles[0].properties['tile_ref']"]
    assert from_tile is instance


def test_tileset_and_tile_properties(tiled_map, resolution):
    tileset = tiled_map.tilesets[1]
    target = resolution.resolved["tilesets[1].properties['tileset_ref']"]
    assert target is tiled_map.layers[0].tiled_objects[1]
    assert tileset.properties["tileset_ref"].id == 2


def test_original_values_preserved(tiled_map):
    # the raw property values are still there after parsing
    raw = tiled_map.properties["spawn"]
    assert isinstance(raw, ObjectRef)
    assert raw.id == 4
    assert int(raw.value) == 4

    # object properties too
    raw = tiled_map.layers[0].tiled_objects[0].properties["target"]
    assert isinstance(raw, ObjectRef)
    assert raw.id == 4


def test_missing_id_error(tiled_map, resolution):
    error = resolution.errors[
        "layers[1].tiled_objects[4].properties['missing_target']"
    ]
    assert error.path == "layers[1].tiled_objects[4].properties['missing_target']"
    assert "999" in error.message


def test_zero_id_error(tiled_map, resolution):
    error = resolution.errors["layers[1].tiled_objects[4].properties['zero_target']"]
    assert "0" in error.message


def test_bad_type_error(tiled_map, resolution):
    error = resolution.errors["layers[1].tiled_objects[4].properties['bad_target']"]
    assert "nope" in error.message


def test_duplicate_id_error(tiled_map, resolution):
    error = resolution.errors["layers[1].tiled_objects[4].properties['dup_target']"]
    assert "7" in error.message
    assert "multiple" in error.message


def test_circular_reference_errors(tiled_map, resolution):
    first = resolution.errors["layers[1].tiled_objects[1].properties['target']"]
    second = resolution.errors["layers[1].tiled_objects[2].properties['target']"]
    assert "Circular" in first.message
    assert "5" in first.message and "6" in first.message
    assert "Circular" in second.message

    # circular references are not left in the resolved results
    assert "layers[1].tiled_objects[1].properties['target']" not in resolution.resolved
    assert "layers[1].tiled_objects[2].properties['target']" not in resolution.resolved


def test_resolved_and_errors_do_not_overlap(resolution):
    assert not set(resolution.resolved).intersection(resolution.errors)
    for path, error in resolution.errors.items():
        assert error.path == path


def test_missing_template_file_json():
    with pytest.raises(ObjectTemplateError) as exc_info:
        parse_map(BROKEN_TEMPLATE_TESTS / "missing_json" / "map.json")
    assert "no_such_template.json" in str(exc_info.value)


def test_missing_template_file_tmx():
    with pytest.raises(ObjectTemplateError) as exc_info:
        parse_map(BROKEN_TEMPLATE_TESTS / "missing_tmx" / "map.tmx")
    assert "no_such_template.tx" in str(exc_info.value)


def test_corrupt_template_file():
    with pytest.raises(ObjectTemplateError) as exc_info:
        parse_map(BROKEN_TEMPLATE_TESTS / "corrupt_json" / "map.json")
    assert "bad_template.json" in str(exc_info.value)
