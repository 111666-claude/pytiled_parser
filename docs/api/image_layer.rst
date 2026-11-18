.. _image_layer_api:
Image Layer Placements
======================

This module provides a rendering agnostic query for determining where the
images backing image layers need to be drawn in order to fill a viewport. It
accounts for the map's parallax origin, layer offsets (including offsets
accumulated through nested group layers), effective parallax factors,
effective opacity, visibility and the independent ``repeat_x`` and
``repeat_y`` flags.

The query never loads image data itself. Instead the caller provides an
``image_size_query`` callable which maps the path stored on each image layer
to its pixel :class:`pytiled_parser.common_types.Size`.

A typical call looks like this::

    from pytiled_parser import OrderedPair, Rect, parse_map, visible_image_layer_placements

    tiled_map = parse_map("level.tmx")
    camera = OrderedPair(200, 100)
    viewport = Rect(200, 100, 1280, 720)

    def image_size_query(image_path):
        # Resolve the path against the map's directory and read the image's
        # header, for example with PIL's Image.open(path).size.
        ...

    placements = visible_image_layer_placements(
        tiled_map, camera, viewport, image_size_query
    )

    for placement in placements:
        # Draw placement.image from placement.source_rect into
        # placement.viewport_clip_rect, which lies within placement.target_rect,
        # using placement.effective_opacity.
        ...

The same query is also available as a method on
:class:`pytiled_parser.tiled_map.TiledMap`.

ImageLayerPlacement
^^^^^^^^^^^^^^^^^^^

.. autoclass:: pytiled_parser.image_layer.ImageLayerPlacement
    :members:

visible_image_layer_placements
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. autofunction:: pytiled_parser.image_layer.visible_image_layer_placements
