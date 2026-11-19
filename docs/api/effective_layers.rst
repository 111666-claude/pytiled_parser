.. _effective_layers_api:
Effective Layers
================

The parser stores layers exactly as they appear in the map file. Group
layers do not bake their opacity, offset or tint color into their children,
and nothing pre-computes which tile layers actually cover a given pixel.
This module performs that work on demand, treating the parsed layer objects
as read-only.

Tiled draws layers back-to-front in document order, with the children of a
group layer drawn in place of the group itself. The effective values of a
tile layer are composed from the layer itself and every group ancestor:

* **opacity** is the product of all opacities in the chain.
* **offset** is the sum of all offsets in the chain, in pixels.
* **tint color** is the channel-wise product of all tint colors in the
  chain, or ``None`` when no tint applies.

A layer and everything below it is skipped when any ancestor is hidden.

Functions
^^^^^^^^^

.. autofunction:: pytiled_parser.effective_layers.get_effective_tile_layers

.. autofunction:: pytiled_parser.effective_layers.iter_effective_tile_layers

.. autofunction:: pytiled_parser.effective_layers.decode_gid

Classes
^^^^^^^

.. autoclass:: pytiled_parser.effective_layers.EffectiveTileLayer
    :members:

.. autoclass:: pytiled_parser.effective_layers.DecodedGID
    :members:

Exceptions
^^^^^^^^^^

.. autoclass:: pytiled_parser.exception.CyclicLayerGroupError
    :members:

.. autoclass:: pytiled_parser.exception.InvalidTintColorError
    :members:

.. autoclass:: pytiled_parser.exception.UnsupportedOrientationError
    :members:
