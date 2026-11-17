.. _tile_collision_api:
Tile Collision
==============

Helpers to extract the collision shapes drawn in Tiled's tile collision editor
(stored on the tileset) from the tile layers of an orthogonal map, reprojected
into world pixel coordinates.

Layer and group offsets are accumulated, and the GID flip flags (horizontal,
vertical, and diagonal) are applied to the geometry. Rectangle, ellipse, and
polygon collision objects are supported. Only orthogonal maps are supported.


extract_tile_collisions
^^^^^^^^^^^^^^^^^^^^^^^

.. autofunction:: pytiled_parser.tile_collision.extract_tile_collisions


TileCollision
^^^^^^^^^^^^^

.. autoclass:: pytiled_parser.tile_collision.TileCollision
    :members:

CollisionRectangle
^^^^^^^^^^^^^^^^^^

.. autoclass:: pytiled_parser.tile_collision.CollisionRectangle

CollisionEllipse
^^^^^^^^^^^^^^^^

.. autoclass:: pytiled_parser.tile_collision.CollisionEllipse

CollisionPolygon
^^^^^^^^^^^^^^^^

.. autoclass:: pytiled_parser.tile_collision.CollisionPolygon
