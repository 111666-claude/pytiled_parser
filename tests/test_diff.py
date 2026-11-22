"""Tests for the map difference report."""

import copy
import os
import pickle
from pathlib import Path

import pytest

from pytiled_parser import parse_map
from pytiled_parser.common_types import OrderedPair, Size
from pytiled_parser.diff import (
    ADDED,
    MODIFIED,
    MOVED,
    REMAPPED,
    REMOVED,
    REPLACED,
    RESIZED,
    apply_map_diff,
    diff_maps,
)
from pytiled_parser.layer import Chunk, LayerGroup, ObjectLayer, TileLayer
from pytiled_parser.tiled_object import Rectangle, Tile

TESTS_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
TEST_DATA = TESTS_DIR / "test_data"
DIFF_TESTS = TEST_DATA / "diff_tests"

FIXTURES = [
    pytest.param("json", id="json"),
    pytest.param("tmx", id="tmx"),
]


def load(version: str, parser_type: str):
    return parse_map(DIFF_TESTS / version / f"map.{parser_type}")


@pytest.fixture(params=FIXTURES)
def map_pair(request):
    return load("v1", request.param), load("v2", request.param)


def _layer_ids(layer):
    ids = [layer.id]
    if isinstance(layer, LayerGroup):
        for child in layer.layers or []:
            ids.extend(_layer_ids(child))
    return ids


def _assert_replays(old, new, report):
    replayed = apply_map_diff(old, report)
    assert diff_maps(replayed, new).is_empty
    return replayed


# ---------------------------------------------------------------------------
# Minimal change set on the fixed level
# ---------------------------------------------------------------------------


def test_fixed_level_minimal_diff(map_pair):
    old, new = map_pair
    report = diff_maps(old, new)

    # Tick order: the group moved above the ground layer, and only those two
    # layers changed. The object layer itself is untouched.
    assert [(c.layer_id, c.change) for c in report.layer_changes] == [
        (3, MOVED),
        (1, MODIFIED),
    ]
    assert report.layer_changes[1].field_changes == {"visible": (True, False)}

    # One modified, one removed and one added object.
    assert [(c.object_id, c.change) for c in report.object_changes] == [
        (10, MODIFIED),
        (11, REMOVED),
        (12, ADDED),
    ]
    assert report.object_changes[0].field_changes == {
        "coordinates": (OrderedPair(64, 32), OrderedPair(96, 32))
    }
    assert [
        (p.name, p.change, p.before, p.after)
        for p in report.object_changes[0].property_changes
    ] == [
        ("awake", ADDED, None, True),
        ("hp", MODIFIED, 3, 7),
    ]

    # Exactly two cells changed, in two different layers.
    assert [(c.layer_id, c.coordinate, c.change) for c in report.cell_changes] == [
        (1, (3, 4), MODIFIED),
        (4, (2, 1), MODIFIED),
    ]
    assert report.cell_changes[0].old.gid == 20
    assert report.cell_changes[0].new.gid == 21
    assert report.cell_changes[1].old.gid == 2
    assert report.cell_changes[1].new.gid == 9

    assert report.map_changes == ()
    assert report.tileset_changes == ()
    assert report.chunk_changes == ()
    assert report.warnings == ()


def test_fixed_level_replay_equivalence(map_pair):
    old, new = map_pair
    _assert_replays(old, new, diff_maps(old, new))


def test_report_is_deterministic(map_pair):
    old, new = map_pair
    first = diff_maps(old, new)
    second = diff_maps(old, new)
    assert first == second
    assert repr(first) == repr(second)


def test_inputs_are_not_mutated(map_pair):
    old, new = map_pair
    old_before = pickle.dumps(old)
    new_before = pickle.dumps(new)
    report = diff_maps(old, new)
    apply_map_diff(old, report)
    assert pickle.dumps(old) == old_before
    assert pickle.dumps(new) == new_before


def test_empty_diff_for_identical_maps(map_pair):
    old, _ = map_pair
    report = diff_maps(old, old)
    assert report.is_empty
    assert report.warnings == ()


# ---------------------------------------------------------------------------
# Layer identity
# ---------------------------------------------------------------------------


def test_same_name_different_id_is_not_a_rename(map_pair):
    old, _ = map_pair
    new = copy.deepcopy(old)
    # Change only the id of the object layer, keeping its name.
    new.layers[1].id = 42

    report = diff_maps(old, new)
    changes = {(c.layer_name, c.change) for c in report.layer_changes}
    assert ("Objects", ADDED) in changes
    assert ("Objects", REMOVED) in changes
    assert not any(c.change == MODIFIED for c in report.layer_changes)


def test_group_reorder_reports_only_reordered_layer(map_pair):
    old, _ = map_pair
    new = copy.deepcopy(old)
    group = new.layers.pop(2)
    new.layers.insert(0, group)

    report = diff_maps(old, new)
    # Only the group moved; the two layers it passed keep their payloads.
    moved = [c for c in report.layer_changes if c.change == MOVED]
    assert [c.layer_id for c in moved] == [3]
    assert report.cell_changes == ()
    assert report.object_changes == ()
    _assert_replays(old, new, report)


def test_layer_insert_does_not_mark_siblings_moved(map_pair):
    old, _ = map_pair
    new = copy.deepcopy(old)
    extra = copy.deepcopy(new.layers[0])
    extra.id = 99
    extra.name = "Extra"
    new.layers.insert(0, extra)

    report = diff_maps(old, new)
    assert [(c.layer_id, c.change) for c in report.layer_changes] == [(99, ADDED)]
    _assert_replays(old, new, report)


def test_layer_removed_with_children_is_single_entry(map_pair):
    old, _ = map_pair
    new = copy.deepcopy(old)
    del new.layers[2]  # Group with the Decor child

    report = diff_maps(old, new)
    assert [(c.layer_id, c.change) for c in report.layer_changes] == [(3, REMOVED)]
    assert report.cell_changes == ()
    _assert_replays(old, new, report)


def test_layer_type_change_is_replaced(map_pair):
    old, _ = map_pair
    new = copy.deepcopy(old)
    new.layers[1] = ObjectLayer(id=2, name="Objects", tiled_objects=[])
    # Replace it with a tile layer of the same id and name.
    new.layers[1] = TileLayer(id=2, name="Objects", data=[[0, 0, 0, 0]])

    report = diff_maps(old, new)
    assert [(c.layer_id, c.change) for c in report.layer_changes] == [(2, REPLACED)]
    _assert_replays(old, new, report)


def test_layer_visibility_and_offset_changes(map_pair):
    old, _ = map_pair
    new = copy.deepcopy(old)
    new.layers[0].visible = False
    new.layers[0].offset = OrderedPair(10, 20)
    new.layers[0].opacity = 0.5

    report = diff_maps(old, new)
    assert len(report.layer_changes) == 1
    changes = report.layer_changes[0].field_changes
    assert changes["visible"] == (True, False)
    assert changes["offset"] == (OrderedPair(0, 0), OrderedPair(10, 20))
    assert changes["opacity"] == (1, 0.5)


# ---------------------------------------------------------------------------
# Object identity
# ---------------------------------------------------------------------------


def test_object_move_between_layers(map_pair):
    old, _ = map_pair
    # Add a second object layer on both sides so there is a destination.
    destination_layer = ObjectLayer(id=50, name="Destination", tiled_objects=[])
    old = copy.deepcopy(old)
    old.layers.append(copy.deepcopy(destination_layer))
    new = copy.deepcopy(old)

    source = new.layers[1]
    moved = source.tiled_objects.pop(0)
    new.layers[-1].tiled_objects.append(moved)

    report = diff_maps(old, new)
    moved_changes = [c for c in report.object_changes if c.change == MOVED]
    assert [c.object_id for c in moved_changes] == [10]
    assert moved_changes[0].old_layer_path == (2,)
    assert moved_changes[0].new_layer_path == (50,)
    _assert_replays(old, new, report)


def test_object_reorder_uses_minimal_moves(map_pair):
    old, _ = map_pair
    new = copy.deepcopy(old)
    objects = new.layers[1].tiled_objects
    extra = copy.deepcopy(objects[0])
    extra.id = 20
    extra.name = "third"
    objects.append(extra)
    old = copy.deepcopy(new)
    new.layers[1].tiled_objects = [objects[2], objects[0], objects[1]]

    report = diff_maps(old, new)
    # Moving one object to the front is one move; the subsequence that was
    # already in order stays untouched.
    moved = [c.object_id for c in report.object_changes if c.change == MOVED]
    assert moved == [20]
    _assert_replays(old, new, report)


def test_object_property_removed(map_pair):
    old, _ = map_pair
    new = copy.deepcopy(old)
    new.layers[1].tiled_objects[0].properties = {}

    report = diff_maps(old, new)
    assert len(report.object_changes) == 1
    assert [
        (p.name, p.change, p.before) for p in report.object_changes[0].property_changes
    ] == [("hp", REMOVED, 3)]
    _assert_replays(old, new, report)


# ---------------------------------------------------------------------------
# Tiles: flip flags, tileset changes and remaps
# ---------------------------------------------------------------------------


def test_flip_flag_is_a_cell_modification(map_pair):
    old, _ = map_pair
    new = copy.deepcopy(old)
    new.layers[0].data[0][0] |= 0x80000000

    report = diff_maps(old, new)
    assert len(report.cell_changes) == 1
    change = report.cell_changes[0]
    assert change.change == MODIFIED
    assert change.old.flipped_horizontally is False
    assert change.new.flipped_horizontally is True
    assert change.old.tile_id == change.new.tile_id
    _assert_replays(old, new, report)


def test_tileset_firstgid_remap_is_reported_once(map_pair):
    old, _ = map_pair
    new = copy.deepcopy(old)
    tileset = new.tilesets.pop(1)
    tileset.firstgid = 100
    new.tilesets[100] = tileset
    for row in new.layers[0].data:
        for index, gid in enumerate(row):
            row[index] = gid + 99
    for chunk_layer in (new.layers[0], new.layers[2].layers[0]):
        pass
    new.layers[2].layers[0].data = [
        [0 if gid == 0 else gid + 99 for gid in row]
        for row in new.layers[2].layers[0].data
    ]

    report = diff_maps(old, new)
    assert [(c.change, c.matched_by) for c in report.tileset_changes] == [
        (REMAPPED, "identity")
    ]
    assert report.tileset_changes[0].field_changes == {"firstgid": (1, 100)}
    # No cell changes: the cells still point at the same tiles.
    assert report.cell_changes == ()
    _assert_replays(old, new, report)


def test_tileset_content_change_is_on_tileset_not_cells(map_pair):
    old, _ = map_pair
    new = copy.deepcopy(old)
    new.tilesets[1].spacing = 4

    report = diff_maps(old, new)
    assert [(c.change, c.field_changes) for c in report.tileset_changes] == [
        (MODIFIED, {"spacing": (1, 4)})
    ]
    assert report.cell_changes == ()
    _assert_replays(old, new, report)


def test_remapped_tileset_keeps_unaffected_gids(map_pair):
    old, _ = map_pair
    new = copy.deepcopy(old)
    # A flip flag must survive the remap on replay.
    new.layers[0].data[0][0] = new.layers[0].data[0][0] | 0x80000000

    report = diff_maps(old, new)
    replayed = _assert_replays(old, new, report)
    assert replayed.layers[0].data[0][0] & 0x80000000


# ---------------------------------------------------------------------------
# Infinite maps
# ---------------------------------------------------------------------------


def _chunk_layer_map():
    return parse_map(TEST_DATA / "layer_tests" / "infinite_map" / "map.json")


def test_chunk_cell_changes_use_absolute_coordinates():
    old = _chunk_layer_map()
    new = copy.deepcopy(old)
    new.layers[0].chunks[1].data[0][0] = 11

    report = diff_maps(old, new)
    assert report.chunk_changes == ()
    assert [
        (c.coordinate, c.change, c.chunk_coordinate) for c in report.cell_changes
    ] == [((4, 0), MODIFIED, (4, 0))]
    _assert_replays(old, new, report)


def test_added_chunk_is_one_chunk_change():
    old = _chunk_layer_map()
    new = copy.deepcopy(old)
    extra = copy.deepcopy(new.layers[0].chunks[0])
    extra.coordinates = OrderedPair(100, 50)
    new.layers[0].chunks.append(extra)

    report = diff_maps(old, new)
    assert [(c.coordinate, c.change, c.new_size) for c in report.chunk_changes] == [
        ((100, 50), ADDED, (4, 8))
    ]
    assert report.cell_changes == ()
    _assert_replays(old, new, report)


def test_resized_chunk_reports_structure_not_cells():
    old = _chunk_layer_map()
    new = copy.deepcopy(old)
    chunk = new.layers[0].chunks[0]
    chunk.size = Size(2, 8)
    chunk.data = [row[:2] for row in chunk.data]

    report = diff_maps(old, new)
    assert [
        (c.coordinate, c.change, c.old_size, c.new_size) for c in report.chunk_changes
    ] == [((0, 0), RESIZED, (4, 8), (2, 8))]
    assert report.cell_changes == ()
    _assert_replays(old, new, report)


def test_finite_and_chunked_cells_compare_the_same(map_pair):
    old, _ = map_pair
    new = copy.deepcopy(old)
    layer = new.layers[0]
    assert layer.data is not None
    layer.chunks = [
        Chunk(
            coordinates=OrderedPair(0, 0),
            size=Size(len(layer.data[0]), len(layer.data)),
            data=[row[:] for row in layer.data],
        )
    ]
    layer.data = None

    report = diff_maps(old, new)
    assert report.cell_changes == ()
    assert report.chunk_changes == ()
    layer_changes = [c for c in report.layer_changes if c.layer_id == 1]
    assert len(layer_changes) == 1
    assert layer_changes[0].change == MODIFIED
    assert layer_changes[0].field_changes["representation"] == (
        "finite",
        "chunked",
    )
    _assert_replays(old, new, report)


def test_infinite_flag_is_a_map_change():
    old = _chunk_layer_map()
    new = copy.deepcopy(old)
    new.infinite = False

    report = diff_maps(old, new)
    assert [c.field for c in report.map_changes] == ["infinite"]


# ---------------------------------------------------------------------------
# Incompatible maps and missing ids
# ---------------------------------------------------------------------------


def test_map_size_change_is_reported_not_raised(map_pair):
    old, _ = map_pair
    new = copy.deepcopy(old)
    new.map_size = Size(1, 1)

    report = diff_maps(old, new)
    assert [(c.field, c.before, c.after) for c in report.map_changes] == [
        ("map_size", Size(4, 5), Size(1, 1))
    ]
    assert report.is_empty is False


def test_tile_size_change_does_not_hide_cells(map_pair):
    old, _ = map_pair
    new = copy.deepcopy(old)
    new.tile_size = Size(16, 16)
    new.layers[0].data[0][0] = 2

    report = diff_maps(old, new)
    assert [c.field for c in report.map_changes] == ["tile_size"]
    assert len(report.cell_changes) == 1


def test_missing_layer_ids_fall_back_to_structural_path(map_pair):
    old, _ = map_pair
    new = copy.deepcopy(old)
    for layer in old.layers:
        layer.id = None
    for layer in new.layers:
        layer.id = None
    new.layers[0].visible = False

    report = diff_maps(old, new)
    assert len(report.layer_changes) == 1
    change = report.layer_changes[0]
    assert change.matched_by == "path"
    assert change.field_changes == {"visible": (True, False)}
    assert any("has an id" in warning for warning in report.warnings)
    _assert_replays(old, new, report)


def test_one_missing_layer_id_matches_by_structure(map_pair):
    old, _ = map_pair
    new = copy.deepcopy(old)
    old.layers[0].id = None
    new.layers[0].id = None
    new.layers[0].opacity = 0.25

    report = diff_maps(old, new)
    assert [(c.change, c.matched_by) for c in report.layer_changes] == [
        (MODIFIED, "path")
    ]


def test_missing_object_ids_fall_back_to_structural_path(map_pair):
    old, _ = map_pair
    new = copy.deepcopy(old)
    for layer in (old.layers[1], new.layers[1]):
        layer.tiled_objects = [Rectangle(id=1, name="a", coordinates=OrderedPair(0, 0))]
    new.layers[1].tiled_objects[0].coordinates = OrderedPair(8, 8)

    report = diff_maps(old, new)
    assert len(report.object_changes) == 1
    assert report.object_changes[0].change == MODIFIED
    assert report.object_changes[0].matched_by == "id"
    _assert_replays(old, new, report)


def test_duplicate_ids_are_warned_and_paired_in_order(map_pair):
    old, _ = map_pair
    new = copy.deepcopy(old)
    new.layers[1].id = 1

    report = diff_maps(old, new)
    assert any("duplicate layer id" in warning for warning in report.warnings)


# ---------------------------------------------------------------------------
# Replay across the whole existing corpus
# ---------------------------------------------------------------------------


def _all_maps():
    paths = []
    for path in sorted((TEST_DATA / "layer_tests").glob("*/map.json")):
        if "zstd" in str(path) or "unknown" in str(path):
            continue
        paths.append(path)
    for path in sorted((TEST_DATA / "map_tests").glob("*/map.json")):
        if "invalid" in str(path):
            continue
        paths.append(path)
    return paths


@pytest.mark.parametrize("path", _all_maps(), ids=lambda p: p.parent.name)
def test_identical_corpus_maps_have_empty_diff(path):
    old = parse_map(path)
    report = diff_maps(old, old)
    assert report.is_empty


@pytest.mark.parametrize("path", _all_maps(), ids=lambda p: p.parent.name)
def test_corpus_replay_roundtrip_with_edited_layer(path):
    old = parse_map(path)
    new = copy.deepcopy(old)
    _edit_somewhere(new)
    report = diff_maps(old, new)
    _assert_replays(old, new, report)


def _edit_somewhere(map_):
    """Apply one change of whatever kind the map supports."""

    for layer in map_.layers:
        if isinstance(layer, TileLayer):
            if layer.data:
                for row_index, row in enumerate(layer.data):
                    for column_index, gid in enumerate(row):
                        row[column_index] = (gid + 1) % 48 + 1
                        return
            if layer.chunks:
                chunk = layer.chunks[0]
                chunk.data[0][0] = (chunk.data[0][0] + 1) % 48 + 1
                return


def test_apply_map_diff_leaves_original_untouched(map_pair):
    old, new = map_pair
    report = diff_maps(old, new)
    replayed = apply_map_diff(old, report)
    assert replayed is not old
    # Mutating the replay must not leak back into the input.
    replayed.layers[0].name = "Changed"
    assert old.layers[0].name != "Changed"


def test_apply_map_diff_on_empty_report_is_equivalent(map_pair):
    old, _ = map_pair
    replayed = apply_map_diff(old, diff_maps(old, old))
    assert diff_maps(replayed, old).is_empty
    assert replayed.layers[0].name == old.layers[0].name


def test_tile_object_gid_change_is_reported(map_pair):
    old, _ = map_pair
    new = copy.deepcopy(old)
    tile_object = Tile(id=30, gid=5, coordinates=OrderedPair(0, 0), size=Size(32, 32))
    old.layers[1].tiled_objects.append(copy.deepcopy(tile_object))
    new.layers[1].tiled_objects.append(copy.deepcopy(tile_object))
    new.layers[1].tiled_objects[-1].gid = 6

    report = diff_maps(old, new)
    change = [c for c in report.object_changes if c.object_id == 30][0]
    assert change.change == MODIFIED
    assert change.field_changes["gid"][0].gid == 5
    assert change.field_changes["gid"][1].gid == 6
    _assert_replays(old, new, report)


def test_layer_moved_into_new_group(map_pair):
    old, _ = map_pair
    new = copy.deepcopy(old)
    child = new.layers[2].layers.pop(0)
    new.layers.append(LayerGroup(id=70, name="Target", layers=[child]))

    report = diff_maps(old, new)
    assert [(c.layer_id, c.change) for c in report.layer_changes] == [
        (70, ADDED),
        (4, MOVED),
    ]
    _assert_replays(old, new, report)


def test_layer_moved_out_of_group(map_pair):
    old, _ = map_pair
    new = copy.deepcopy(old)
    new.layers.append(new.layers[2].layers.pop(0))

    report = diff_maps(old, new)
    assert [(c.layer_id, c.change) for c in report.layer_changes] == [(4, MOVED)]
    _assert_replays(old, new, report)


def test_layer_copied_into_new_group(map_pair):
    old, _ = map_pair
    new = copy.deepcopy(old)
    copied = copy.deepcopy(new.layers[2].layers[0])
    new.layers.append(LayerGroup(id=70, name="Target", layers=[copied]))

    report = diff_maps(old, new)
    _assert_replays(old, new, report)


def test_visibility_change_only_touches_that_layer(map_pair):
    old, _ = map_pair
    new = copy.deepcopy(old)
    new.layers[2].layers[0].visible = False

    report = diff_maps(old, new)
    assert [(c.layer_id, c.change) for c in report.layer_changes] == [(4, MODIFIED)]
    assert report.layer_changes[0].field_changes == {"visible": (True, False)}
    _assert_replays(old, new, report)


def test_tileset_added_and_removed(map_pair):
    old, _ = map_pair
    new = copy.deepcopy(old)
    removed = new.tilesets.pop(1)
    added = copy.deepcopy(removed)
    added.name = "second_tiles"
    added.firstgid = 200
    new.tilesets[200] = added

    report = diff_maps(old, new)
    assert sorted(c.change for c in report.tileset_changes) == [ADDED, REMOVED]
    # The cells still reference global ids that no longer resolve, so they are
    # reported as changed rather than silently dropped.
    assert report.cell_changes
    assert all(c.old is not None and c.new is not None for c in report.cell_changes)
    _assert_replays(old, new, report)


def test_tileset_matched_by_name_when_image_moves(map_pair):
    old, _ = map_pair
    new = copy.deepcopy(old)
    new.tilesets[1].image = Path("elsewhere/other.png")

    report = diff_maps(old, new)
    assert [(c.change, c.matched_by) for c in report.tileset_changes] == [
        (MODIFIED, "name")
    ]
    assert any("matched by name" in warning for warning in report.warnings)
    _assert_replays(old, new, report)


def test_added_and_removed_cells(map_pair):
    old, _ = map_pair
    new = copy.deepcopy(old)
    new.layers[0].data[0][0] = 0  # cleared
    new.layers[0].data[0][1] = 30  # changed
    # A new non-zero cell in an all-zero decor layer cell.
    new.layers[2].layers[0].data[0][0] = 12

    report = diff_maps(old, new)
    ground = [c for c in report.cell_changes if c.layer_id == 1]
    assert [(c.coordinate, c.change) for c in ground] == [
        ((0, 0), REMOVED),
        ((1, 0), MODIFIED),
    ]
    decor = [c for c in report.cell_changes if c.layer_id == 4]
    assert [(c.coordinate, c.change) for c in decor] == [((0, 0), ADDED)]
    _assert_replays(old, new, report)


def test_removed_chunk_is_one_chunk_change():
    old = _chunk_layer_map()
    new = copy.deepcopy(old)
    del new.layers[0].chunks[1]

    report = diff_maps(old, new)
    assert [(c.coordinate, c.change) for c in report.chunk_changes] == [
        ((4, 0), REMOVED)
    ]
    assert report.cell_changes == ()
    _assert_replays(old, new, report)


def test_tileset_tile_definition_change():
    old = _chunk_layer_map()
    new = copy.deepcopy(old)
    new.tilesets[1].spacing = 3

    report = diff_maps(old, new)
    assert report.tileset_changes[0].change == MODIFIED
    assert "spacing" in report.tileset_changes[0].field_changes


def test_gid_beyond_tile_count_still_compares_by_tile_id(map_pair):
    old, _ = map_pair
    new = copy.deepcopy(old)
    # A semantically invalid but parseable gid is still resolved through the
    # highest matching firstgid, so the report stays structural.
    new.layers[0].data[0][0] = 0x0FFFFFF0

    report = diff_maps(old, new)
    assert len(report.cell_changes) == 1
    assert report.cell_changes[0].new.tile_id == 0x0FFFFFF0 - 1
    _assert_replays(old, new, report)


def test_tileset_tile_definitions_added_removed_and_modified():
    from pytiled_parser.tileset import Tile as TilesetTile

    old = _chunk_layer_map()
    new = copy.deepcopy(old)
    old.tilesets[1].tiles = {0: TilesetTile(id=0), 1: TilesetTile(id=1)}
    new.tilesets[1].tiles = {1: TilesetTile(id=1, opacity=0), 2: TilesetTile(id=2)}

    report = diff_maps(old, new)
    tile_changes = report.tileset_changes[0].tile_changes
    assert [(c.tile_id, c.change) for c in tile_changes] == [
        (0, REMOVED),
        (1, MODIFIED),
        (2, ADDED),
    ]
    _assert_replays(old, new, report)


def test_map_metadata_changes(map_pair):
    old, _ = map_pair
    new = copy.deepcopy(old)
    new.background_color = (0, 0, 0, 255)
    new.properties = {"weather": "rain"}
    new.orientation = "isometric"

    report = diff_maps(old, new)
    assert [c.field for c in report.map_changes] == [
        "background_color",
        "orientation",
        "properties",
    ]
    _assert_replays(old, new, report)


def test_layer_custom_property_change(map_pair):
    old, _ = map_pair
    new = copy.deepcopy(old)
    new.layers[0].properties = {"locked": True}

    report = diff_maps(old, new)
    assert report.layer_changes[0].change == MODIFIED
    assert "properties" in report.layer_changes[0].field_changes
    _assert_replays(old, new, report)


def test_layer_opacity_and_parallax_changes(map_pair):
    old, _ = map_pair
    new = copy.deepcopy(old)
    new.layers[0].parallax_factor = OrderedPair(0.5, 0.5)
    new.layers[0].tint_color = (255, 0, 0, 255)

    report = diff_maps(old, new)
    changes = report.layer_changes[0].field_changes
    assert changes["parallax_factor"][1] == (0.5, 0.5)
    assert changes["tint_color"][1] == (255, 0, 0, 255)


def test_child_moved_out_of_removed_group_survives(map_pair):
    old, _ = map_pair
    new = copy.deepcopy(old)
    # Move Decor out, then remove the now-empty Group.
    child = new.layers[2].layers.pop(0)
    new.layers.append(child)
    del new.layers[2]

    report = diff_maps(old, new)
    assert sorted((c.layer_id, c.change) for c in report.layer_changes) == [
        (3, REMOVED),
        (4, MOVED),
    ]
    _assert_replays(old, new, report)


def test_child_moved_to_root_as_group_removed_in_same_save(map_pair):
    old, _ = map_pair
    new = copy.deepcopy(old)
    group = new.layers[2]
    child = group.layers.pop(0)
    new.layers.insert(0, child)
    new.layers.remove(group)

    report = diff_maps(old, new)
    _assert_replays(old, new, report)
