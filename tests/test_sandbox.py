"""Tests for the optional reference path sandbox.

These tests build throwaway level packs in temporary directories so that no
fixture data needs to be trusted. They cover:

* blocking ``../`` traversal, absolute paths, mixed separators and symlink
  escapes,
* allowing same-directory, sub-directory and in-pack ``../`` references,
* distinguishable errors for each kind of violation, including reference
  cycles and missing targets,
* unchanged behavior when no sandbox is supplied,
* running the existing real fixtures inside a sandbox.
"""

import json
from pathlib import Path
from typing import Optional

import pytest

from pytiled_parser import (
    PathSandbox,
    SandboxAbsolutePath,
    SandboxCycle,
    SandboxPathNotFound,
    SandboxPathOutsideRoot,
    parse_map,
    parse_tileset,
    parse_world,
)
from pytiled_parser.sandbox import _looks_absolute, resolve_reference

PNG_HEADER = b"\x89PNG\r\n\x1a\n"


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def write_png(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(PNG_HEADER)
    return path


def json_tileset(name: str = "tileset", image: str = "image.png") -> str:
    return json.dumps(
        {
            "columns": 1,
            "image": image,
            "imageheight": 8,
            "imagewidth": 8,
            "margin": 0,
            "name": name,
            "spacing": 0,
            "tilecount": 1,
            "tileheight": 8,
            "tilewidth": 8,
            "type": "tileset",
            "version": "1.9",
        }
    )


def json_map(
    tileset_source: str = "tiles/tileset.json",
    template: Optional[str] = None,
    image_layer: Optional[str] = None,
) -> str:
    tilesets = []
    if tileset_source is not None:
        tilesets.append({"firstgid": 1, "source": tileset_source})

    layers = [
        {
            "data": [0, 0, 0, 0],
            "height": 2,
            "id": 1,
            "name": "Tile Layer",
            "opacity": 1,
            "type": "tilelayer",
            "visible": True,
            "width": 2,
            "x": 0,
            "y": 0,
        }
    ]

    if template is not None:
        layers.append(
            {
                "draworder": "topdown",
                "id": 2,
                "name": "Objects",
                "objects": [
                    {
                        "id": 1,
                        "template": template,
                        "visible": True,
                        "width": 8,
                        "height": 8,
                        "x": 0,
                        "y": 0,
                    }
                ],
                "opacity": 1,
                "type": "objectgroup",
                "visible": True,
                "x": 0,
                "y": 0,
            }
        )

    if image_layer is not None:
        layers.append(
            {
                "id": 3,
                "image": image_layer,
                "name": "Image Layer",
                "opacity": 1,
                "type": "imagelayer",
                "visible": True,
                "x": 0,
                "y": 0,
            }
        )

    return json.dumps(
        {
            "compressionlevel": 0,
            "height": 2,
            "infinite": False,
            "layers": layers,
            "nextobjectid": 1,
            "nextlayerid": 4,
            "orientation": "orthogonal",
            "renderorder": "right-down",
            "tiledversion": "1.9.0",
            "tileheight": 8,
            "tilewidth": 8,
            "tilesets": tilesets,
            "type": "map",
            "version": "1.9",
            "width": 2,
        }
    )


def tmx_tileset(image: str = "image.png") -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<tileset version="1.9" tiledversion="1.9.0" name="tileset" '
        'tilewidth="8" tileheight="8" tilecount="1" columns="1">'
        f'<image source="{image}" width="8" height="8"/>'
        "</tileset>"
    )


def tmx_map(tileset_source: str = "tiles/tileset.tsx") -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<map version="1.9" tiledversion="1.9.0" orientation="orthogonal" '
        'renderorder="right-down" width="2" height="2" tilewidth="8" '
        'tileheight="8" infinite="0" nextlayerid="2" nextobjectid="1">'
        f'<tileset firstgid="1" source="{tileset_source}"/>'
        '<layer id="1" name="Tile Layer" width="2" height="2">'
        "<data>0,0,0,0</data>"
        "</layer>"
        "</map>"
    )


def json_rectangle_template(
    tileset_source: Optional[str] = None,
) -> str:
    doc = {
        "type": "template",
        "object": {
            "id": 1,
            "name": "templated",
            "width": 8,
            "height": 8,
            "x": 0,
            "y": 0,
            "rotation": 0,
            "visible": True,
        },
    }
    if tileset_source is not None:
        doc["tileset"] = {"firstgid": 1, "source": tileset_source}
    return json.dumps(doc)


@pytest.fixture
def pack(tmp_path: Path) -> Path:
    """A minimal, fully legal level pack."""
    root = tmp_path / "pack"
    write_png(root / "tiles" / "image.png")
    write(root / "tiles" / "tileset.json", json_tileset())
    write(root / "map.json", json_map())
    write(root / "tiles" / "tileset.tsx", tmx_tileset())
    write(root / "map.tmx", tmx_map())
    (tmp_path / "outside").mkdir()
    write_png(tmp_path / "outside" / "secret.png")
    write(tmp_path / "outside" / "secret.json", json_tileset(image="secret.png"))
    return root


# ---------------------------------------------------------------------------
# Construction and entry-point validation
# ---------------------------------------------------------------------------


def test_sandbox_rejects_missing_root(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        PathSandbox(tmp_path / "does_not_exist")


def test_sandbox_rejects_file_root(tmp_path: Path) -> None:
    file_path = write(tmp_path / "not_a_dir.txt", "data")
    with pytest.raises(ValueError):
        PathSandbox(file_path)


def test_sandbox_accepts_multiple_roots(pack: Path, tmp_path: Path) -> None:
    other = tmp_path / "outside"
    sandbox = PathSandbox([pack, other])
    assert sandbox.check_path(other / "secret.png") == (other / "secret.png").resolve()


def test_entry_map_outside_root_is_blocked(pack: Path, tmp_path: Path) -> None:
    other_map = write(
        tmp_path / "outside" / "map.json", json_map(tileset_source="secret.json")
    )
    sandbox = PathSandbox(pack)
    with pytest.raises(SandboxPathOutsideRoot):
        parse_map(other_map, sandbox=sandbox)


def test_entry_map_missing_is_not_found(tmp_path: Path) -> None:
    sandbox = PathSandbox(tmp_path)
    with pytest.raises(SandboxPathNotFound):
        parse_map(tmp_path / "missing.json", sandbox=sandbox)


# ---------------------------------------------------------------------------
# Default behavior is unchanged
# ---------------------------------------------------------------------------


def test_no_sandbox_allows_parent_traversal(pack: Path) -> None:
    # A reference leaving the pack resolves exactly as it did before.
    write(pack / "map_escape.json", json_map(tileset_source="../outside/secret.json"))
    parsed = parse_map(pack / "map_escape.json")
    assert 1 in parsed.tilesets


def test_no_sandbox_allows_absolute_path(pack: Path) -> None:
    absolute = str(pack / "tiles" / "tileset.json")
    write(pack / "map_abs.json", json_map(tileset_source=absolute))
    parsed = parse_map(pack / "map_abs.json")
    assert 1 in parsed.tilesets


# ---------------------------------------------------------------------------
# Blocking violations
# ---------------------------------------------------------------------------


def test_relative_parent_traversal_is_blocked(pack: Path) -> None:
    write(pack / "bad.json", json_map(tileset_source="../outside/secret.json"))
    sandbox = PathSandbox(pack)
    with pytest.raises(SandboxPathOutsideRoot) as exc_info:
        parse_map(pack / "bad.json", sandbox=sandbox)
    assert exc_info.value.reference == "../outside/secret.json"
    assert exc_info.value.resolved is not None


def test_absolute_posix_path_is_blocked(pack: Path) -> None:
    absolute = str((pack / "tiles" / "tileset.json").resolve())
    write(pack / "bad_abs.json", json_map(tileset_source=absolute))
    sandbox = PathSandbox(pack)
    with pytest.raises(SandboxAbsolutePath) as exc_info:
        parse_map(pack / "bad_abs.json", sandbox=sandbox)
    assert exc_info.value.reference == absolute


@pytest.mark.parametrize(
    "reference",
    [
        "C:/outside/secret.json",
        "C:\\outside\\secret.json",
        "//server/share/secret.json",
        "\\\\server\\share\\secret.json",
    ],
)
def test_windows_style_absolute_paths_are_blocked_on_any_platform(
    pack: Path, reference: str
) -> None:
    write(pack / "bad_win.json", json_map(tileset_source=reference))
    sandbox = PathSandbox(pack)
    with pytest.raises(SandboxAbsolutePath):
        parse_map(pack / "bad_win.json", sandbox=sandbox)


def test_mixed_separators_are_normalized_and_checked(pack: Path) -> None:
    # A legal mixed-separator reference inside the pack must still resolve.
    write(
        pack / "mixed_ok.json",
        json_map(tileset_source="tiles\\tileset.json"),
    )
    sandbox = PathSandbox(pack)
    parsed = parse_map(pack / "mixed_ok.json", sandbox=sandbox)
    assert parsed.tilesets[1].image == (pack / "tiles" / "image.png").resolve()

    # A mixed-separator traversal attempt must be blocked as an outside path.
    write(
        pack / "mixed_bad.json",
        json_map(tileset_source="tiles\\..\\..\\outside\\secret.json"),
    )
    with pytest.raises(SandboxPathOutsideRoot):
        parse_map(pack / "mixed_bad.json", sandbox=sandbox)


def test_missing_tileset_target_is_not_found(pack: Path) -> None:
    write(
        pack / "missing.json",
        json_map(tileset_source="tiles/does_not_exist.json"),
    )
    sandbox = PathSandbox(pack)
    with pytest.raises(SandboxPathNotFound) as exc_info:
        parse_map(pack / "missing.json", sandbox=sandbox)
    assert exc_info.value.reference == "tiles/does_not_exist.json"


def test_missing_image_target_is_not_found(pack: Path) -> None:
    write(
        pack / "tiles" / "broken.json",
        json_tileset(image="image_missing.png"),
    )
    sandbox = PathSandbox(pack)
    with pytest.raises(SandboxPathNotFound):
        parse_tileset(pack / "tiles" / "broken.json", sandbox=sandbox)


def test_symlink_escape_is_blocked(pack: Path) -> None:
    link = pack / "tiles" / "escape_link.json"
    try:
        link.symlink_to(pack.parent / "outside" / "secret.json")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not supported on this platform/filesystem")

    write(pack / "via_link.json", json_map(tileset_source="tiles/escape_link.json"))
    sandbox = PathSandbox(pack)
    with pytest.raises(SandboxPathOutsideRoot):
        parse_map(pack / "via_link.json", sandbox=sandbox)


def test_symlink_escape_through_image_directory(pack: Path) -> None:
    link_dir = pack / "linked_assets"
    try:
        link_dir.symlink_to(pack.parent / "outside", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not supported on this platform/filesystem")

    write(
        pack / "tiles" / "linked.json",
        json_tileset(image="../linked_assets/secret.png"),
    )
    sandbox = PathSandbox(pack)
    with pytest.raises(SandboxPathOutsideRoot):
        parse_tileset(pack / "tiles" / "linked.json", sandbox=sandbox)


def test_in_pack_symlink_is_allowed(pack: Path) -> None:
    try:
        (pack / "tiles" / "image_alias.png").symlink_to(pack / "tiles" / "image.png")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not supported on this platform/filesystem")

    write(
        pack / "tiles" / "aliased.json",
        json_tileset(image="image_alias.png"),
    )
    sandbox = PathSandbox(pack)
    tileset = parse_tileset(pack / "tiles" / "aliased.json", sandbox=sandbox)
    assert tileset.image == (pack / "tiles" / "image.png").resolve()


# ---------------------------------------------------------------------------
# Allowed references
# ---------------------------------------------------------------------------


def test_same_directory_and_subdirectory_references_resolve(pack: Path) -> None:
    write_png(pack / "root_image.png")
    write(pack / "root_tileset.json", json_tileset(image="root_image.png"))
    write(pack / "map_same_dir.json", json_map(tileset_source="root_tileset.json"))

    sandbox = PathSandbox(pack)
    parsed = parse_map(pack / "map_same_dir.json", sandbox=sandbox)
    assert parsed.tilesets[1].image == (pack / "root_image.png").resolve()

    sub_parsed = parse_map(pack / "map.json", sandbox=sandbox)
    assert sub_parsed.tilesets[1].image == (pack / "tiles" / "image.png").resolve()


def test_in_pack_parent_traversal_is_allowed(pack: Path) -> None:
    write_png(pack / "shared" / "common.png")
    write(
        pack / "tiles" / "deep" / "tileset.json",
        json_tileset(image="../../shared/common.png"),
    )
    write(
        pack / "deep_map.json",
        json_map(tileset_source="tiles/deep/tileset.json"),
    )

    sandbox = PathSandbox(pack)
    parsed = parse_map(pack / "deep_map.json", sandbox=sandbox)
    assert parsed.tilesets[1].image == (pack / "shared" / "common.png").resolve()


def test_image_layer_is_sandboxed(pack: Path) -> None:
    write_png(pack / "background.png")
    write(
        pack / "image_layer_map.json",
        json_map(image_layer="background.png"),
    )
    sandbox = PathSandbox(pack)
    parsed = parse_map(pack / "image_layer_map.json", sandbox=sandbox)
    image_layer = next(layer for layer in parsed.layers if layer.name == "Image Layer")
    assert image_layer.image == (pack / "background.png").resolve()

    write(
        pack / "image_layer_bad.json",
        json_map(image_layer="../outside/secret.png"),
    )
    with pytest.raises(SandboxPathOutsideRoot):
        parse_map(pack / "image_layer_bad.json", sandbox=sandbox)


# ---------------------------------------------------------------------------
# Templates and cycles
# ---------------------------------------------------------------------------


def test_legal_template_resolves_under_sandbox(pack: Path) -> None:
    write(pack / "template.json", json_rectangle_template())
    write(pack / "template_map.json", json_map(template="template.json"))
    sandbox = PathSandbox(pack)
    parsed = parse_map(pack / "template_map.json", sandbox=sandbox)
    objects = parsed.layers[1].tiled_objects
    assert objects[0].name == "templated"


def test_template_outside_root_is_blocked(pack: Path) -> None:
    outside_template = pack.parent / "outside" / "template.json"
    write(outside_template, json_rectangle_template())
    write(pack / "bad_template_map.json", json_map(template="../outside/template.json"))
    sandbox = PathSandbox(pack)
    with pytest.raises(SandboxPathOutsideRoot):
        parse_map(pack / "bad_template_map.json", sandbox=sandbox)


def test_self_referencing_template_is_a_cycle(pack: Path) -> None:
    # The template's tileset source points back at the template itself.
    write(
        pack / "cycle.json",
        json_rectangle_template(tileset_source="cycle.json"),
    )
    write(pack / "cycle_map.json", json_map(template="cycle.json"))
    sandbox = PathSandbox(pack)
    with pytest.raises(SandboxCycle):
        parse_map(pack / "cycle_map.json", sandbox=sandbox)


def test_cycle_guard_catches_nested_re_entry(pack: Path) -> None:
    # Unit-level guarantee that the active-file stack rejects both direct
    # self-references and mutual re-entry of an already open file.
    target = pack / "tiles" / "tileset.json"
    other = pack / "map.json"
    sandbox = PathSandbox(pack)

    with sandbox.visiting(target), pytest.raises(SandboxCycle):
        with sandbox.visiting(target):
            pass

    with sandbox.visiting(target), sandbox.visiting(other), pytest.raises(SandboxCycle):
        with sandbox.visiting(target):
            pass

    # The stack unwinds correctly, so later parses are unaffected.
    with sandbox.visiting(other):
        pass


def test_template_naming_itself_as_tileset_is_a_cycle(pack: Path) -> None:
    # End-to-end loop: a template whose tileset source points back at itself
    # re-enters an already active file load and must raise SandboxCycle rather
    # than recurse until the stack overflows.
    write(
        pack / "loop_template.json",
        json_rectangle_template(tileset_source="loop_template.json"),
    )
    write(pack / "loop_map.json", json_map(template="loop_template.json"))
    sandbox = PathSandbox(pack)
    with pytest.raises(SandboxCycle):
        parse_map(pack / "loop_map.json", sandbox=sandbox)


# ---------------------------------------------------------------------------
# TMX maps
# ---------------------------------------------------------------------------


def test_tmx_map_allowed_under_sandbox(pack: Path) -> None:
    sandbox = PathSandbox(pack)
    parsed = parse_map(pack / "map.tmx", sandbox=sandbox)
    assert parsed.tilesets[1].image == (pack / "tiles" / "image.png").resolve()


def test_tmx_map_traversal_blocked(pack: Path) -> None:
    write(
        pack / "bad.tmx",
        tmx_map(tileset_source="../outside/secret.json"),
    )
    sandbox = PathSandbox(pack)
    with pytest.raises(SandboxPathOutsideRoot):
        parse_map(pack / "bad.tmx", sandbox=sandbox)


# ---------------------------------------------------------------------------
# The library's own, real-world fixtures run unchanged under a sandbox
# ---------------------------------------------------------------------------

TESTS_DIR = Path(__file__).resolve().parent
TEST_DATA = TESTS_DIR / "test_data"
# test_data is the smallest common root containing every map, tileset,
# template and image referenced by the committed fixtures.
FIXTURE_SANDBOX_ROOT = TEST_DATA

FIXTURE_MAPS = [
    ("map_tests", "external_tileset_dif_dir"),
    ("map_tests", "no_layers"),
    ("map_tests", "no_background_color"),
    ("map_tests", "hexagonal"),
    ("map_tests", "embedded_tileset"),
    ("map_tests", "template"),
    ("map_tests", "cross_format_tileset"),
]


@pytest.mark.parametrize("parser_type", ["json", "tmx"])
@pytest.mark.parametrize("fixture", FIXTURE_MAPS)
def test_committed_fixtures_parse_under_sandbox(
    parser_type: str, fixture: tuple
) -> None:
    fixture_dir = TEST_DATA.joinpath(*fixture)
    map_file = fixture_dir / ("map.json" if parser_type == "json" else "map.tmx")

    sandbox = PathSandbox(FIXTURE_SANDBOX_ROOT)
    parsed = parse_map(map_file, sandbox=sandbox)

    # Every tileset image the parser recorded must resolve inside the sandbox.
    for tileset in parsed.tilesets.values():
        if tileset.image is not None:
            assert sandbox.check_path(tileset.image) == tileset.image.resolve()


def test_committed_cross_template_fixture_runs_under_sandbox() -> None:
    # The cross-format template fixture raises NotImplementedError for its
    # unsupported format combination. It also contains a pre-existing dangling
    # image reference (../../images/tile_04.png does not exist; the images live
    # under test_data/images/), which the historical parser never opened and so
    # never noticed. Under a sandbox that missing target is reported distinctly
    # as SandboxPathNotFound. Either way the sandbox is what governs the walk.
    cross_dir = TESTS_DIR / "test_cross_template"
    sandbox = PathSandbox(TESTS_DIR)
    for map_file in ("map.json", "map.tmx"):
        with pytest.raises((NotImplementedError, SandboxPathNotFound)):
            parse_map(cross_dir / map_file, sandbox=sandbox)


def test_cross_template_escape_is_still_blocked() -> None:
    # A broad root (tests/) still blocks a reference that leaves it entirely.
    cross_dir = TESTS_DIR / "test_cross_template"
    outside = TESTS_DIR.parent / "README.md"
    sandbox = PathSandbox(TESTS_DIR)
    with pytest.raises((SandboxPathOutsideRoot, SandboxAbsolutePath)):
        # Point the sandbox directly at a path outside the tests/ root.
        sandbox.check_path(outside)


# ---------------------------------------------------------------------------
# Worlds
# ---------------------------------------------------------------------------


def test_world_references_are_sandboxed(pack: Path) -> None:
    world = {
        "maps": [
            {"fileName": "map.json", "height": 16, "width": 16, "x": 0, "y": 0},
            {
                "fileName": "../outside/secret.json",
                "height": 1,
                "width": 1,
                "x": 1,
                "y": 1,
            },
        ]
    }
    write(pack / "world.json", json.dumps(world))
    sandbox = PathSandbox(pack)
    with pytest.raises(SandboxPathOutsideRoot):
        parse_world(pack / "world.json", sandbox=sandbox)

    world["maps"].pop()
    write(pack / "world_ok.json", json.dumps(world))
    parsed = parse_world(pack / "world_ok.json", sandbox=sandbox)
    assert len(parsed.maps) == 1
    assert parsed.maps[0].map_file == (pack / "map.json").resolve()


# ---------------------------------------------------------------------------
# Unit tests for the platform-independent absolute path detector
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "reference",
    [
        "/etc/passwd",
        "/",
        "C:/Windows/system32",
        "c:\\windows\\system32",
        "//server/share/file",
        "\\\\server\\share\\file",
    ],
)
def test_looks_absolute_true(reference: str) -> None:
    assert _looks_absolute(reference)


@pytest.mark.parametrize(
    "reference",
    [
        "tiles/image.png",
        "./tiles/image.png",
        "../outside/file",
        "tiles\\image.png",
        "..\\..\\outside",
        "relative",
    ],
)
def test_looks_absolute_false(reference: str) -> None:
    assert not _looks_absolute(reference)


def test_resolve_reference_without_sandbox_matches_legacy(tmp_path: Path) -> None:
    assert resolve_reference(None, tmp_path, "a/b.json") == tmp_path / "a" / "b.json"


def test_sandbox_roots_are_real_paths(tmp_path: Path) -> None:
    link = tmp_path / "link_to_pack"
    real = tmp_path / "real_pack"
    real.mkdir()
    try:
        link.symlink_to(real, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not supported on this platform/filesystem")

    sandbox = PathSandbox(link)
    assert sandbox.roots == [real.resolve()]
    # Reaching the real directory through the symlink stays inside the root.
    target = real / "map.json"
    write(target, "{}")
    assert sandbox.check_path(link / "map.json") == target.resolve()
