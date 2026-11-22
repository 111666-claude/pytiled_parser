.. _viewport_api:
Viewport Queries
================

This module answers the question a renderer asks every frame: *which tiles of
this map are inside the current camera rectangle?* It works the same for
finite maps (one rectangular ``data`` grid) and infinite maps (a list of
chunks), and it never expands the whole map.

:func:`~pytiled_parser.viewport.get_viewport_tiles` and
:func:`~pytiled_parser.viewport.iter_viewport_tiles` accept either a single
:class:`~pytiled_parser.layer.TileLayer`, or a whole
:class:`~pytiled_parser.tiled_map.TiledMap` (or a layer group / iterable of
layers). For a map, every tile layer is queried in draw order with group layer
children expanded in place. The equivalent convenience methods
:meth:`~pytiled_parser.layer.TileLayer.get_viewport_tiles` and
:meth:`~pytiled_parser.layer.TileLayer.iter_viewport_tiles` are available for
the single layer case.

Coordinates are global tile coordinates, and may be negative. The query
rectangle is half-open, so ``TileRect(0, 0, 2, 2)`` covers the four tiles at
``(0, 0)``, ``(1, 0)``, ``(0, 1)`` and ``(1, 1)``. A rectangle with a zero or
negative width/height returns nothing without touching the layer data.

Tiles are returned as :class:`~pytiled_parser.viewport.ViewportTile` objects
carrying the owning layer, its ID and name, the global cell coordinate, the
raw GID (flip flags included) and the chunk the tile came from. Within one
layer results are ordered row by row: ascending global ``y``, then ascending
global ``x``.

Only the cells that intersect the rectangle are inspected. Chunks that do not
intersect it are skipped entirely, and a viewport that covers a huge sparse
area still only reads the data that exists.

Overlapping chunks
^^^^^^^^^^^^^^^^^^

Files written by Tiled never contain overlapping chunks, but hand edited,
merged or patched files can. Chunks are applied in list order, matching how
Tiled's own reader loads them, so a later chunk wins over an earlier one for
any shared cell. An empty cell in the later chunk clears the tile placed by
the earlier chunk.

Flip flags
^^^^^^^^^^

The raw GID is returned unchanged, including the horizontal, vertical and
diagonal flip bits and the hexagonal 120 degree rotation bit. A cell is
considered empty only when the GID with those flag bits masked away is zero.

Example
^^^^^^^

.. code-block:: python

    from pytiled_parser import OrderedPair, get_viewport_tiles, parse_map

    tiled_map = parse_map("map.json")

    # Camera showing tile columns -8..23 and rows -8..15.
    visible = get_viewport_tiles(tiled_map, (-8, -8, 32, 24))
    for tile in visible:
        print(tile.layer_name, tile.coordinates, tile.gid)

    # A single layer uses the same rectangle object.
    layer = tiled_map.layers[0]
    for tile in layer.iter_viewport_tiles((-8, -8, 32, 24)):
        if tile.gid:
            draw(tile.coordinates, tile.gid)

TileRect
^^^^^^^^

.. autoclass:: pytiled_parser.viewport.TileRect
    :members:

ViewportTile
^^^^^^^^^^^^

.. autoclass:: pytiled_parser.viewport.ViewportTile
    :members:

get_viewport_tiles
^^^^^^^^^^^^^^^^^^

.. autofunction:: pytiled_parser.viewport.get_viewport_tiles

iter_viewport_tiles
^^^^^^^^^^^^^^^^^^^

.. autofunction:: pytiled_parser.viewport.iter_viewport_tiles
