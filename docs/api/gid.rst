.. _gid_api:

Tile Source (GID) Lookup
========================

A renderer receives raw global tile IDs (GIDs) from tile layer data and tile
objects. A raw GID does not only identify a tile: its four most significant
bits may carry flip and rotation flags, and the remaining bits index into one
of the map's tilesets. The functions and types on this page perform the whole
resolution in one step:

1. The flip flags are split off of the plain GID without modifying the
   original value.
2. The tileset whose ``firstgid`` range contains the plain GID is located.
3. The local tile ID inside of that tileset is computed.
4. The image file and the pixel rectangle to crop out of it are determined,
   for both spritesheet tilesets and image collection tilesets.
5. The horizontal, vertical and diagonal flips and the tileset drawing offset
   are reported.

The simplest entry point is :func:`get_tile_source`, which accepts either the
parsed :class:`pytiled_parser.TiledMap` or its ``tilesets`` mapping::

    import pytiled_parser

    source = pytiled_parser.get_tile_source(raw_gid, tiled_map)
    if source is None:
        ...  # GID 0 is the empty cell
    else:
        image = load(source.image_path)
        crop = image.crop(source.rect)  # x, y, width, height in pixels
        if source.transform.flipped_horizontally:
            ...

GID ``0`` denotes an empty cell and always resolves to ``None``.

Flag constants and decoding
----------------------------

.. autodata:: pytiled_parser.gid.FLIP_HORIZONTALLY_FLAG
.. autodata:: pytiled_parser.gid.FLIP_VERTICALLY_FLAG
.. autodata:: pytiled_parser.gid.FLIP_DIAGONALLY_FLAG
.. autodata:: pytiled_parser.gid.ROTATED_HEXAGONAL_120_FLAG
.. autodata:: pytiled_parser.gid.GID_MASK
.. autodata:: pytiled_parser.gid.MAX_GID

.. autofunction:: pytiled_parser.gid.decode_gid
.. autofunction:: pytiled_parser.gid.find_tilesets
.. autofunction:: pytiled_parser.gid.resolve_tile
.. autofunction:: pytiled_parser.gid.get_tile_source
.. autofunction:: pytiled_parser.gid.get_tile_source_from_tileset

Result types
------------

.. autoclass:: pytiled_parser.gid.GIDTransform
    :members:

.. autoclass:: pytiled_parser.gid.TileSource
    :members:

Exceptions
----------

Every exception raised by the lookup inherits from :class:`GIDError` and keeps
the original, unmodified raw GID as its ``gid`` attribute. Overlapping
tileset ranges still resolve deterministically: the tileset with the highest
``firstgid`` is selected and attached to the
:class:`TilesetOverlapError` as ``selected_tileset``.

.. autoclass:: pytiled_parser.gid_exception.GIDError
    :members:

.. autoclass:: pytiled_parser.gid_exception.InvalidGIDError
    :members:

.. autoclass:: pytiled_parser.gid_exception.GIDNotFoundError
    :members:

.. autoclass:: pytiled_parser.gid_exception.TilesetOverlapError
    :members:

.. autoclass:: pytiled_parser.gid_exception.TileImageError
    :members:

.. autoclass:: pytiled_parser.gid_exception.TileImageNotFoundError
    :members:

.. autoclass:: pytiled_parser.gid_exception.TileRectError
    :members:

See the `Tiled global tile ID reference
<https://doc.mapeditor.org/en/stable/reference/global-tile-ids/>`_ for the
flag bit layout.
