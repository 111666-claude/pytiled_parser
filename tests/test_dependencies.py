"""Tests for the map dependency manifest."""

import os
from pathlib import Path

import attr
import pytest

from pytiled_parser import UnknownFormat, parse_map
from pytiled_parser.dependencies import (
    DependencyManifest,
    IssueCode,
    ResourceType,
    check_dependencies,
    collect_dependencies,
)

TESTS_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
DEPENDENCY_TESTS = TESTS_DIR / "test_data" / "dependency_tests"

NESTED = DEPENDENCY_TESTS / "nested"
SAME_NAME = DEPENDENCY_TESTS / "same_name"
ISSUES = DEPENDENCY_TESTS / "issues"
CYCLE = DEPENDENCY_TESTS / "cycle"
EMBEDDED = DEPENDENCY_TESTS / "embedded"
MALFORMED = DEPENDENCY_TESTS / "malformed"

# The nested fixture exists in a JSON and a TMX variant. Each variant
# references files of the other format on purpose, so both formats are
# exercised for every kind of reference.
NESTED_MAP = {"json": NESTED / "map.json", "tmx": NESTED / "map.tmx"}
TEMPLATE_EXT = {"json": "json", "tmx": "tx"}
TILESET_EXT = {"json": "json", "tmx": "tsx"}
OTHER_TILESET_EXT = {"json": "tsx", "tmx": "json"}


def resolve(relative):
    return (DEPENDENCY_TESTS / relative).resolve()


def by_location(manifest):
    return {
        (source.file.name, source.location): (
            dependency.path,
            source.reference,
            source.resource_type,
        )
        for dependency in manifest.dependencies
        for source in dependency.sources
    }


@pytest.mark.parametrize("parser_type", ["json", "tmx"])
def test_nested_manifest_lists_every_file_once_in_order(parser_type):
    manifest = collect_dependencies(NESTED_MAP[parser_type], check_exists=True)

    # Depth first. tileset_a is walked completely first, then tileset_b,
    # then the templates (template_a pulls in tileset_a again, which is
    # already listed, and template_b pulls in tileset_b's other variant).
    assert manifest.paths == [
        resolve("nested/tilesets/tileset_a." + TILESET_EXT[parser_type]),
        resolve("nested/art/one/shared.png"),
        resolve("nested/art/two/shared.png"),
        resolve("nested/props/config.txt"),
        resolve("nested/tilesets/tileset_b." + OTHER_TILESET_EXT[parser_type]),
        resolve("nested/templates/template_a." + TEMPLATE_EXT[parser_type]),
        resolve("nested/templates/template_b." + TEMPLATE_EXT[parser_type]),
        resolve("nested/tilesets/tileset_b." + TILESET_EXT[parser_type]),
        resolve("nested/templates/template_c." + TEMPLATE_EXT[parser_type]),
    ]
    assert len(manifest.paths) == len(set(manifest.paths))


@pytest.mark.parametrize("parser_type", ["json", "tmx"])
def test_nested_manifest_resource_types(parser_type):
    manifest = collect_dependencies(NESTED_MAP[parser_type])
    entries = {
        str(dependency.path.relative_to(DEPENDENCY_TESTS.resolve())): dependency
        for dependency in manifest.dependencies
    }

    assert (
        entries["nested/tilesets/tileset_a." + TILESET_EXT[parser_type]].resource_type
        == ResourceType.TILESET
    )
    assert (
        entries[
            "nested/tilesets/tileset_b." + OTHER_TILESET_EXT[parser_type]
        ].resource_type
        == ResourceType.TILESET
    )
    assert (
        entries["nested/art/one/shared.png"].resource_type == ResourceType.TILESET_IMAGE
    )
    assert entries["nested/art/two/shared.png"].resource_type == ResourceType.TILE_IMAGE
    assert (
        entries["nested/props/config.txt"].resource_type == ResourceType.FILE_PROPERTY
    )
    assert (
        entries[
            "nested/templates/template_a." + TEMPLATE_EXT[parser_type]
        ].resource_type
        == ResourceType.TEMPLATE
    )


def test_image_layer_source_is_recorded_with_deep_path():
    manifest = collect_dependencies(NESTED / "map.json")
    locations = by_location(manifest)

    path, reference, resource_type = locations[
        ("map.json", "layers[1].layers[0].layers[0].image")
    ]
    assert path == resolve("nested/art/two/shared.png")
    assert reference == "art/two/shared.png"
    assert resource_type == ResourceType.IMAGE_LAYER_IMAGE


def test_duplicate_reference_is_listed_once_with_all_sources():
    manifest = collect_dependencies(NESTED / "map.json")
    shared = resolve("nested/art/one/shared.png")

    matches = [
        dependency for dependency in manifest.dependencies if dependency.path == shared
    ]
    assert len(matches) == 1

    # The same file is a sprite sheet for two tilesets, an individual tile
    # image for one of them, and the image of an image layer. Different
    # relative spellings resolve to the same canonical path.
    assert [(source.file.name, source.location) for source in matches[0].sources] == [
        ("tileset_a.json", "tileset.image"),
        ("tileset_b.tsx", "tileset.image"),
        ("tileset_b.tsx", "tileset.tiles[0].image"),
        ("map.json", "layers[0].image"),
        ("tileset_b.json", "tileset.image"),
        ("tileset_b.json", "tileset.tiles[0].image"),
    ]
    assert {source.resource_type for source in matches[0].sources} == {
        ResourceType.TILESET_IMAGE,
        ResourceType.TILE_IMAGE,
        ResourceType.IMAGE_LAYER_IMAGE,
    }


def test_source_records_the_file_the_reference_was_written_in():
    manifest = collect_dependencies(NESTED / "map.json")
    locations = by_location(manifest)

    path, _, resource_type = locations[("template_a.json", "tileset.source")]
    assert path == resolve("nested/tilesets/tileset_a.json")
    assert resource_type == ResourceType.TILESET


@pytest.mark.parametrize("parser_type", ["json", "tmx"])
def test_file_properties_are_collected_from_every_container(parser_type):
    manifest = collect_dependencies(NESTED_MAP[parser_type])
    locations = by_location(manifest)
    config = resolve("nested/props/config.txt")
    map_name = "map.json" if parser_type == "json" else "map.tmx"

    # The same file property is referenced from the map, a layer, an object,
    # a template's object, a tileset and a tile.
    references = [
        (file, location)
        for (file, location), (path, _, resource_type) in locations.items()
        if resource_type == ResourceType.FILE_PROPERTY
    ]
    assert {locations[reference][0] for reference in references} == {config}

    map_locations = {location for file, location in references if file == map_name}
    assert map_locations == {
        "properties['config']",
        "layers[0].properties['layer config']",
        "layers[2].objects[2].properties['object config']",
    }

    assert (
        "tileset_a." + TILESET_EXT[parser_type],
        "tileset.properties['config']",
    ) in references
    assert (
        "tileset_a." + TILESET_EXT[parser_type],
        "tileset.tiles[1].properties['tile config']",
    ) in references
    assert (
        "template_c." + TEMPLATE_EXT[parser_type],
        "object.properties['template config']",
    ) in references


def test_relative_paths_are_resolved_against_each_referencing_file():
    # Both tilesets reference a file called "shared.png" next to themselves.
    # Those are different files and both have to be listed under their own
    # canonical paths.
    manifest = check_dependencies(SAME_NAME / "map.json")
    assert manifest.paths == [
        resolve("same_name/tilesets/a/tileset.json"),
        resolve("same_name/tilesets/a/shared.png"),
        resolve("same_name/tilesets/b/tileset.json"),
        resolve("same_name/tilesets/b/shared.png"),
    ]
    assert not manifest.issues

    manifest = check_dependencies(SAME_NAME / "map.tmx")
    assert manifest.paths == [
        resolve("same_name/tilesets/a/tileset.tsx"),
        resolve("same_name/tilesets/a/shared.png"),
        resolve("same_name/tilesets/b/tileset.tsx"),
        resolve("same_name/tilesets/b/shared.png"),
    ]
    assert not manifest.issues


@pytest.mark.parametrize("parser_type", ["json", "tmx"])
def test_embedded_tilesets_are_resolved_against_their_host_file(parser_type):
    map_path = EMBEDDED / ("map." + parser_type)

    # The map's embedded tileset lives in the map file; the tileset embedded
    # in the template lives in the template file, next to a same-named
    # directory. The two "sheet.png"/"tile.png" pairs are different files.
    manifest = check_dependencies(map_path)
    assert manifest.paths == [
        resolve("embedded/art/sheet.png"),
        resolve("embedded/art/tile.png"),
        resolve("embedded/templates/embedded." + TEMPLATE_EXT[parser_type]),
        resolve("embedded/templates/sheet.png"),
        resolve("embedded/templates/tile.png"),
        resolve("embedded/props/embedded.txt"),
    ]
    assert not manifest.issues

    locations = {
        (source.file.name, source.location)
        for dependency in manifest.dependencies
        for source in dependency.sources
    }
    assert ("map." + parser_type, "tilesets[0].image") in locations
    assert ("map." + parser_type, "tilesets[0].tiles[0].image") in locations
    assert (
        "embedded." + TEMPLATE_EXT[parser_type],
        "tileset.image",
    ) in locations


def test_map_parsing_is_unaffected():
    map_path = NESTED / "map.json"
    expected = parse_map(map_path)

    manifest = check_dependencies(map_path)
    assert isinstance(manifest, DependencyManifest)
    assert parse_map(map_path) == expected


def test_report_only_does_not_probe_leaf_files():
    manifest = collect_dependencies(ISSUES / "map.json")
    by_name = {dependency.path.name: dependency for dependency in manifest.dependencies}

    # Tilesets and templates have to be read, so their existence is known.
    assert by_name["missing.json"].exists is False
    assert by_name["present.json"].exists is True
    assert by_name["corrupt.json"].exists is True

    # Images and file properties are not probed in report-only mode.
    assert by_name["present.png"].exists is None
    assert by_name["missing.png"].exists is None
    assert by_name["missing.txt"].exists is None

    assert resolve("issues/tilesets/missing.json") in manifest.missing
    assert not any(
        issue.path == resolve("issues/art/missing.png") for issue in manifest.issues
    )


def test_check_dependencies_probes_every_file():
    manifest = check_dependencies(ISSUES / "map.json")
    by_name = {dependency.path.name: dependency for dependency in manifest.dependencies}

    assert by_name["missing.png"].exists is False
    assert by_name["present.png"].exists is True
    assert by_name["missing.txt"].exists is False

    assert resolve("issues/art/missing.png") in manifest.missing
    assert manifest.has_issues(IssueCode.MISSING)


@pytest.mark.parametrize("parser_type", ["json", "tmx"])
def test_missing_structural_file_is_always_reported(parser_type):
    ext = TILESET_EXT[parser_type]
    manifest = collect_dependencies(ISSUES / ("map." + parser_type))

    missing = [issue for issue in manifest.issues if issue.code == IssueCode.MISSING]
    issue = next(
        issue
        for issue in missing
        if issue.path == resolve("issues/tilesets/missing." + ext)
    )
    assert "tilesets[0].source" in issue.message
    assert issue.source.reference == "tilesets/missing." + ext


@pytest.mark.parametrize("parser_type", ["json", "tmx"])
def test_missing_template_is_reported_with_context(parser_type):
    ext = TEMPLATE_EXT[parser_type]
    manifest = collect_dependencies(ISSUES / ("map." + parser_type))

    issue = next(
        issue
        for issue in manifest.issues
        if issue.code == IssueCode.MISSING
        and issue.path == resolve("issues/templates/missing." + ext)
    )
    assert "objects[0].template" in issue.message
    assert issue.source is not None
    assert issue.source.location == "layers[3].objects[0].template"
    assert issue.source.reference == "templates/missing." + ext


def test_unreadable_and_objectless_templates_are_unsupported():
    manifest = collect_dependencies(ISSUES / "map.tmx")
    unsupported = {
        issue.path: issue
        for issue in manifest.issues
        if issue.code == IssueCode.UNSUPPORTED
    }

    corrupt = unsupported[resolve("issues/templates/corrupt.tx")]
    assert "Could not read template" in corrupt.message

    no_object = unsupported[resolve("issues/templates/no_object.tx")]
    assert "does not contain an object" in no_object.message
    # The tileset the objectless template does reference is still collected.
    assert resolve("issues/tilesets/present.tsx") in manifest.paths


def test_unsupported_references_have_no_path():
    manifest = collect_dependencies(ISSUES / "map.json")

    bad_values = [
        issue
        for issue in manifest.issues
        if issue.code == IssueCode.UNSUPPORTED
        and "not a usable file path" in issue.message
    ]

    # A file property whose value is a number, and one whose value is blank.
    assert len(bad_values) == 2
    for issue in bad_values:
        assert issue.path is None
        assert issue.source is not None
        assert issue.source.location.startswith("properties[")
    assert {issue.source.reference for issue in bad_values} == {"5", "   "}


def test_image_without_source_is_unsupported():
    manifest = collect_dependencies(ISSUES / "sourceless_image.tmx")

    assert len(manifest.issues) == 1
    issue = manifest.issues[0]
    assert issue.code == IssueCode.UNSUPPORTED
    assert "has no source attribute" in issue.message
    assert "layers[0].image" in issue.message
    assert not manifest.dependencies


def test_outside_root_is_reported_but_still_listed():
    manifest = check_dependencies(ISSUES / "map.json")
    outside = resolve("existing_outside.txt")

    assert outside in manifest.paths
    assert {issue.code for issue in manifest.issues if issue.path == outside} == {
        IssueCode.OUTSIDE_ROOT
    }

    # Widening the root removes the issue but keeps the dependency.
    wide = check_dependencies(ISSUES / "map.json", root=DEPENDENCY_TESTS)
    assert outside in wide.paths
    assert not any(issue.path == outside for issue in wide.issues)


@pytest.mark.parametrize("parser_type", ["json", "tmx"])
def test_cycles_are_reported_and_not_followed(parser_type):
    manifest = collect_dependencies(CYCLE / ("map." + parser_type))

    cycle_issues = [issue for issue in manifest.issues if issue.code == IssueCode.CYCLE]
    assert len(cycle_issues) == 1
    # Both members of the cycle are listed, and the loop stops there.
    cycle_tileset = CYCLE / ("tilesets/cycle_a." + TILESET_EXT[parser_type])
    cycle_template = CYCLE / ("templates/cycle." + TEMPLATE_EXT[parser_type])
    assert cycle_issues[0].path == cycle_tileset.resolve()
    assert "Circular tileset reference detected" in cycle_issues[0].message
    assert manifest.paths == [cycle_tileset.resolve(), cycle_template.resolve()]


@pytest.mark.parametrize(
    "tileset, expected",
    [
        ("bad_tiles.json", "tile is not a JSON object"),
        ("bad_properties.json", "property is not a JSON object"),
        ("bad_layers.json", "object is not a JSON object"),
    ],
)
def test_malformed_tileset_contents_are_unsupported(tileset, expected):
    manifest = collect_dependencies(MALFORMED / "map.json")

    issue = next(
        issue
        for issue in manifest.issues
        if issue.code == IssueCode.UNSUPPORTED
        and issue.path == resolve("malformed/tilesets/" + tileset)
    )
    assert expected in issue.message
    assert issue.source is not None

    # Whatever is well formed in the map is still collected.
    assert resolve("malformed/art/missing.png") in manifest.paths


def test_malformed_templates_are_unsupported():
    manifest = collect_dependencies(MALFORMED / "map.tmx")

    issue = next(
        issue
        for issue in manifest.issues
        if issue.code == IssueCode.UNSUPPORTED
        and issue.path == resolve("malformed/templates/bad_template.json")
    )
    # A JSON array is not a template object.
    assert "template is not a JSON object" in issue.message
    assert issue.source is not None
    assert issue.source.location == "tileset.tiles[0].objects[0].template"

    # The holder tileset's own file property is still collected.
    assert resolve("existing_outside.txt") in manifest.paths


def test_malformed_map_entries_are_unsupported():
    manifest = collect_dependencies(MALFORMED / "shapes.json")

    # A malformed entry does not abort the walk: the well formed layer after
    # the bad one is still collected.
    assert manifest.paths == [resolve("malformed/art/missing.png")]

    unsupported = [
        issue for issue in manifest.issues if issue.code == IssueCode.UNSUPPORTED
    ]
    assert len(unsupported) == 2
    assert all(issue.path is None for issue in unsupported)
    assert any("tilesets[0]" in issue.message for issue in unsupported)
    assert any("layers[0]" in issue.message for issue in unsupported)


def test_json_map_that_is_not_an_object_raises_unknown_format():
    with pytest.raises(UnknownFormat):
        collect_dependencies(MALFORMED / "list_map.json")


def test_malformed_template_tileset_is_unsupported():
    manifest = collect_dependencies(MALFORMED / "map.json")

    issue = next(
        issue
        for issue in manifest.issues
        if issue.code == IssueCode.UNSUPPORTED
        and issue.path == resolve("malformed/templates/bad_tileset.json")
    )
    assert "tileset is not a JSON object" in issue.message
    assert issue.source is not None
    assert issue.source.location == "layers[1].objects[0].template"


def test_malformed_map_properties_are_unsupported():
    manifest = collect_dependencies(MALFORMED / "map.json")

    issue = next(
        issue
        for issue in manifest.issues
        if issue.code == IssueCode.UNSUPPORTED
        and issue.message.startswith("Could not process properties")
    )
    assert issue.path is None
    # The map's other references are still collected.
    assert resolve("malformed/art/missing.png") in manifest.paths


def test_file_property_without_value_is_unsupported():
    manifest = collect_dependencies(MALFORMED / "map.tmx")

    issue = next(
        issue
        for issue in manifest.issues
        if issue.code == IssueCode.UNSUPPORTED and "has no value" in issue.message
    )
    assert issue.path is None
    assert "properties['no value']" in issue.message


def test_manifest_convenience_members():
    manifest = check_dependencies(ISSUES / "map.json")

    assert manifest.paths == [dependency.path for dependency in manifest.dependencies]
    assert manifest.has_issues()
    assert manifest.has_issues(IssueCode.OUTSIDE_ROOT)
    assert not manifest.has_issues("no_such_code")
    assert all(dependency.exists is not None for dependency in manifest.dependencies)


def test_accepts_a_parsed_map():
    map_path = NESTED / "map.json"
    tiled_map = parse_map(map_path)

    from_path = collect_dependencies(map_path, check_exists=True)
    from_map = collect_dependencies(tiled_map, check_exists=True)

    assert from_map.paths == from_path.paths
    assert from_map.map_file == from_path.map_file


def test_map_objects_are_not_modified():
    map_path = NESTED / "map.json"
    before = parse_map(map_path)
    before_images = [tileset_.image for tileset_ in before.tilesets.values()]

    check_dependencies(map_path)

    after = parse_map(map_path)
    assert after == before
    assert [tileset_.image for tileset_ in after.tilesets.values()] == before_images
    assert after.layers[0].image == Path("art/one/shared.png")


def test_parsed_map_without_map_file_is_rejected():
    tiled_map = parse_map(NESTED / "map.json")
    tiled_map.map_file = None

    with pytest.raises(ValueError):
        collect_dependencies(tiled_map)


def test_nonexistent_map_raises():
    with pytest.raises(FileNotFoundError):
        collect_dependencies(DEPENDENCY_TESTS / "does_not_exist.json")


def test_dependency_sources_are_immutable():
    manifest = collect_dependencies(NESTED / "map.json")
    source = manifest.dependencies[0].sources[0]

    with pytest.raises(attr.exceptions.FrozenInstanceError):
        source.location = "elsewhere"


def test_invalid_map_raises_unknown_format():
    with pytest.raises(UnknownFormat):
        collect_dependencies(TESTS_DIR / "test_data" / "invalid_format.garbage")
