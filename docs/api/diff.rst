.. _diff_api:
Diff
====

This module compares two already parsed maps and reports exactly which
tiles, objects and layers changed. It is intended for editor preview flows
that want to patch what they already have instead of rebuilding a map.

Layers are matched by their Tiled layer id and objects by their object id.
Names are never used to match entries that have ids, so two layers that share
a name but not an id are reported as one removal and one addition. Entries
without an id fall back to a deterministic structural path and are flagged
with ``matched_by="path"``.

Tile cells are compared by ``(tileset, local tile id)`` plus flip flags rather
than by raw global id. Moving a tileset's ``firstgid`` therefore produces one
``remapped`` tileset change and no cell changes. Infinite maps are compared by
absolute tile coordinates; added, removed or resized chunks are reported once
at chunk level instead of cell by cell.

Every report is sorted deterministically, and neither input map is modified.

Example
-------

.. code-block:: python

    from pathlib import Path

    from pytiled_parser import apply_map_diff, diff_maps, parse_map

    old_map = parse_map(Path("levels/level_1.v1.tmx"))
    new_map = parse_map(Path("levels/level_1.v2.tmx"))

    report = diff_maps(old_map, new_map)
    for cell in report.cell_changes:
        print(cell.layer_id, cell.coordinate, cell.change)

    # Optional: prove the report is complete.
    replayed = apply_map_diff(old_map, report)
    assert diff_maps(replayed, new_map).is_empty

MapDiff
^^^^^^^

.. autoclass:: pytiled_parser.diff.MapDiff
    :members:

LayerChange
^^^^^^^^^^^

.. autoclass:: pytiled_parser.diff.LayerChange
    :members:

ObjectChange
^^^^^^^^^^^^

.. autoclass:: pytiled_parser.diff.ObjectChange
    :members:

CellChange
^^^^^^^^^^

.. autoclass:: pytiled_parser.diff.CellChange
    :members:

ChunkChange
^^^^^^^^^^^

.. autoclass:: pytiled_parser.diff.ChunkChange
    :members:

TilesetChange
^^^^^^^^^^^^^

.. autoclass:: pytiled_parser.diff.TilesetChange
    :members:

TileRef
^^^^^^^

.. autoclass:: pytiled_parser.diff.TileRef
    :members:

Functions
^^^^^^^^^

.. autofunction:: pytiled_parser.diff.diff_maps

.. autofunction:: pytiled_parser.diff.apply_map_diff
