.. _hit_test_api:
Hit Testing
===========

This module provides object hit testing, also called point selection. It answers
the same question the Tiled Editor answers when an object is clicked in the map
view: which visible objects are underneath a given map coordinate?

The :func:`~pytiled_parser.hit_test.hit_test` function works on a fully parsed
:class:`~pytiled_parser.tiled_map.TiledMap`. Every visible object whose shape
contains the queried coordinate is returned as a :class:`~pytiled_parser.hit_test.HitResult`,
ordered from the topmost object to the bottommost one according to the map's
draw order.

All five Tiled shape types are supported: rectangles, ellipses, polygons,
polylines and point objects. Text and tile objects are tested against their
bounding rectangle, the same way Tiled selects them. Object rotation, object
layer offsets, group layer offsets and visibility are all taken into account.

Click tolerances, in pixels, can be supplied for shapes that are otherwise
impossible to click precisely. ``line_tolerance`` widens polylines and the
outlines of degenerate shapes, while ``point_tolerance`` widens point objects
and other zero-size shapes.

.. code-block:: python

    from pytiled_parser import parse_map, OrderedPair, hit_test

    tiled_map = parse_map("map.json")
    results = hit_test(tiled_map, OrderedPair(100, 120), line_tolerance=2.0)

    for result in results:
        print(result.object_id, result.layer.name, result.tiled_object.name)

hit_test
^^^^^^^^

.. autofunction:: pytiled_parser.hit_test.hit_test

HitResult
^^^^^^^^^

.. autoclass:: pytiled_parser.hit_test.HitResult
    :members:
