.. _animation_api:
Animation
=========

This module provides helpers for resolving which tile should be displayed in a
tile layer cell at a given point in time.

Tiled stores global tile IDs (GIDs) in tile layer data, and packs the horizontal,
vertical and diagonal flip flags into the highest bits of each GID. A tile may
also have an animation made up of frames, each pointing at another tile in the
same tileset with a duration in milliseconds. The functions here follow those
frame durations, loop the animation indefinitely, and preserve the flip flags of
the cell the animation lives in.

For more information about animations, see
`Tiled's Tile Animation Editor Docs <https://doc.mapeditor.org/en/stable/manual/editing-tilesets/#tile-animation-editor>`_

Functions
^^^^^^^^^

.. automodule:: pytiled_parser.animation
    :members:
