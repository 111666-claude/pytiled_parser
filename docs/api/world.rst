.. _world_api:
World
=====

This module provides an implementation for the World files from Tiled.

See `Tiled's docs for Worlds <https://doc.mapeditor.org/en/stable/manual/worlds/>`_
for more info about worlds and what they do.

The functionality within PyTiled Parser is to load the world and outline the size
and position of each map, and provide the path to the map file. Loading a world
does not automatically load each map within the world, this is so that the game
or engine implementation can decide how to handle map loading.

For scrolling cameras, the ``World`` class also provides rectangular region
queries. ``World.maps_in_region()`` returns the ``WorldMap`` objects that
intersect a viewport without touching the filesystem, while
``World.load_maps()`` parses those maps on demand, caching each file so it is
only parsed once. Callers that do not want any map loading can simply use
``maps_in_region()`` and pass the resulting ``WorldMap`` objects to
``pytiled_parser.parse_map()`` (or ``World.load_world_map()``) themselves.

WorldMap
^^^^^^^^

.. autoclass:: pytiled_parser.world.WorldMap
    :members:

World
^^^^^

.. autoclass:: pytiled_parser.world.World
    :members: