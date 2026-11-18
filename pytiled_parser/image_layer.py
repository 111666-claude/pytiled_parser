"""Queries for visible image layer placements.

This module provides rendering agnostic logic for determining where the
images backing :class:`pytiled_parser.layer.ImageLayer` objects need to be
drawn in order to fill a viewport.

It implements the same positioning rules Tiled itself uses:

* The map's ``parallax_origin`` and the camera centered viewport drive the
  parallax offset, matching Tiled's ``MapScene::parallaxOffset``.
* Layer offsets are accumulated through nested
  :class:`pytiled_parser.layer.LayerGroup` objects, matching Tiled's
  ``Layer::totalOffset``.
* Parallax factors are multiplied together through the group hierarchy,
  matching Tiled's ``Layer::effectiveParallaxFactor``.
* Opacity is multiplied through the group hierarchy and a layer is only
  considered visible when every layer in its ancestry is visible, matching
  Tiled's ``Layer::effectiveOpacity`` and ``Layer::isHidden``.
* The ``repeat_x`` and ``repeat_y`` flags independently control tiling along
  each axis.

The module never loads image data itself. Instead the caller provides an
``image_size_query`` callable which maps an image path to its
:class:`pytiled_parser.common_types.Size`. This keeps the library free of any
image loading dependency while still allowing the query to determine the
placement and clipping of every repeated image.
"""

# pylint: disable=too-few-public-methods

import math
from typing import Callable, List, Optional, Tuple

import attr

from pytiled_parser.common_types import OrderedPair, Rect, Size
from pytiled_parser.layer import ImageLayer, Layer, LayerGroup
from pytiled_parser.tiled_map import TiledMap

# A callable which, given the path stored on an image layer, returns the
# pixel size of that image.
ImageSizeQuery = Callable[[object], Size]


@attr.s(auto_attribs=True, kw_only=True)
class ImageLayerPlacement:
    """A single drawable placement of an image layer's image.

    When an image layer repeats along one or both axes, one placement is
    produced for each repetition that intersects the viewport. When the layer
    does not repeat, at most one placement is produced and only when the
    single image intersects the viewport.

    Attributes:
        layer: The :class:`ImageLayer` that produced this placement.
        image: The image path taken directly from the layer. It is the same
            value that was passed to the image size query.
        target_rect: The rectangle, in the same coordinate space as the
            supplied viewport, where the full image is to be drawn.
        viewport_clip_rect: The intersection of ``target_rect`` and the
            viewport rectangle. Drawing may be clipped to this rectangle.
        source_rect: The region of the source image that maps onto
            ``viewport_clip_rect``. Its size always matches
            ``viewport_clip_rect`` and its position is relative to the top
            left corner of the source image.
        effective_opacity: The layer's own opacity multiplied by the opacity
            of every containing group layer. A value of ``1`` is fully opaque
            and ``0`` is fully transparent.
        repeat_index_x: The repetition index on the X axis. This is always
            ``0`` for layers which do not repeat along X, otherwise negative
            values indicate placements to the left of the base image.
        repeat_index_y: The repetition index on the Y axis. This is always
            ``0`` for layers which do not repeat along Y, otherwise negative
            values indicate placements above the base image.
    """

    layer: ImageLayer
    image: object
    target_rect: Rect
    viewport_clip_rect: Rect
    source_rect: Rect
    effective_opacity: float
    repeat_index_x: int
    repeat_index_y: int


def _intersects(rect_a: Rect, rect_b: Rect) -> bool:
    """Return whether two rectangles share any area.

    Touching edges and zero area rectangles do not count as an intersection,
    which means placements that only graze the viewport border are not
    reported.
    """
    return (
        rect_a.x < rect_b.x + rect_b.width
        and rect_a.x + rect_a.width > rect_b.x
        and rect_a.y < rect_b.y + rect_b.height
        and rect_a.y + rect_a.height > rect_b.y
    )


def _intersection(rect_a: Rect, rect_b: Rect) -> Optional[Rect]:
    """Return the overlapping rectangle of two rectangles, or ``None``."""
    if not _intersects(rect_a, rect_b):
        return None

    left = max(rect_a.x, rect_b.x)
    top = max(rect_a.y, rect_b.y)
    right = min(rect_a.x + rect_a.width, rect_b.x + rect_b.width)
    bottom = min(rect_a.y + rect_a.height, rect_b.y + rect_b.height)

    return Rect(left, top, right - left, bottom - top)


def _visible_repeat_indices(
    base_position: float, image_length: float, viewport: Rect, axis: str
) -> Tuple[int, ...]:
    """Return the repeat indices which intersect the viewport on one axis.

    Args:
        base_position: The position of the un-repeated image on this axis.
        image_length: The size of the image on this axis.
        viewport: The viewport rectangle.
        axis: ``"x"`` for the horizontal axis, anything else for vertical.

    Returns:
        A tuple of repeat indices in ascending order. A non-repeating axis
        always yields ``(0,)``. Zero length images yield an empty tuple.
    """
    if image_length <= 0:
        return ()

    viewport_position = viewport.x if axis == "x" else viewport.y
    viewport_length = viewport.width if axis == "x" else viewport.height

    first_index = math.floor((viewport_position - base_position) / image_length)
    last_index = math.floor(
        (viewport_position + viewport_length - base_position) / image_length
    )

    return tuple(range(first_index, last_index + 1))


def _collect_image_layers(
    layers: List[Layer],
    accumulated_offset: OrderedPair,
    accumulated_parallax: OrderedPair,
    accumulated_opacity: float,
    ancestry_visible: bool,
) -> List[Tuple[ImageLayer, OrderedPair, OrderedPair, float, bool]]:
    """Flatten the layer tree into image layers with inherited state.

    Returns tuples of ``(image_layer, total_offset, effective_parallax_factor,
    effective_opacity, ancestry_visible)`` in layer tree draw order, which is
    the order layers appear in their containing lists with group layers
    expanded in place.
    """
    collected: List[Tuple[ImageLayer, OrderedPair, OrderedPair, float, bool]] = []

    for layer in layers:
        total_offset = OrderedPair(
            accumulated_offset.x + layer.offset.x,
            accumulated_offset.y + layer.offset.y,
        )
        effective_parallax = OrderedPair(
            accumulated_parallax.x * layer.parallax_factor.x,
            accumulated_parallax.y * layer.parallax_factor.y,
        )
        effective_opacity = accumulated_opacity * layer.opacity
        layer_visible = ancestry_visible and layer.visible

        if isinstance(layer, LayerGroup):
            if layer.layers:
                collected.extend(
                    _collect_image_layers(
                        layer.layers,
                        total_offset,
                        effective_parallax,
                        effective_opacity,
                        layer_visible,
                    )
                )
        elif isinstance(layer, ImageLayer):
            collected.append(
                (
                    layer,
                    total_offset,
                    effective_parallax,
                    effective_opacity,
                    layer_visible,
                )
            )

    return collected


def visible_image_layer_placements(
    tiled_map: TiledMap,
    camera_position: OrderedPair,
    viewport: Rect,
    image_size_query: ImageSizeQuery,
) -> List[ImageLayerPlacement]:
    """Return all visible image layer placements within a viewport.

    Args:
        tiled_map: The parsed map whose image layers should be queried.
        camera_position: The top left corner of the viewport in map pixel
            coordinates. It is used together with ``viewport`` to derive the
            viewport center that drives parallax scrolling.
        viewport: The viewport rectangle in map pixel coordinates, with its
            top left corner at ``camera_position``. A rectangle with a zero or
            negative width or height is invalid and raises a ``ValueError``,
            since nothing could be visible in it.
        image_size_query: A callable which receives the image path stored on
            each visible image layer and returns its pixel
            :class:`Size`. Implementations commonly resolve the path against
            the map's directory and load only the image's header information.

    Returns:
        A list of :class:`ImageLayerPlacement` objects. The list is ordered
        first by layer draw order (a depth first traversal of the layer tree)
        and then by placement order, with X repetitions varying fastest and Y
        repetitions varying slowest. It is stable across repeated calls with
        the same arguments.

    Raises:
        ValueError: If ``viewport`` has a zero or negative width or height.
    """
    if viewport.width <= 0 or viewport.height <= 0:
        raise ValueError(
            "The viewport must have a positive width and height, got "
            f"{viewport.width} x {viewport.height}."
        )

    view_center = OrderedPair(
        camera_position.x + viewport.width / 2,
        camera_position.y + viewport.height / 2,
    )

    image_layers = _collect_image_layers(
        tiled_map.layers,
        OrderedPair(0.0, 0.0),
        OrderedPair(1.0, 1.0),
        1.0,
        True,
    )

    placements: List[ImageLayerPlacement] = []

    for (
        image_layer,
        total_offset,
        effective_parallax,
        effective_opacity,
        is_visible,
    ) in image_layers:
        # A hidden layer, or a layer hidden by one of its parent groups, has
        # no visible placements. Fully transparent layers are skipped as well
        # since drawing them could never change the output.
        if not is_visible or effective_opacity <= 0:
            continue

        # This matches Tiled's MapScene::parallaxOffset. The view center is
        # measured relative to the map's parallax origin and the layer only
        # scrolls by the portion of that movement not covered by its
        # effective parallax factor.
        parallax_offset = OrderedPair(
            (1.0 - effective_parallax.x)
            * (view_center.x - tiled_map.parallax_origin.x),
            (1.0 - effective_parallax.y)
            * (view_center.y - tiled_map.parallax_origin.y),
        )

        base_position = OrderedPair(
            total_offset.x + parallax_offset.x,
            total_offset.y + parallax_offset.y,
        )

        image_size = image_size_query(image_layer.image)

        # Zero sized images cannot cover any pixels, so they never produce
        # placements regardless of the repeat configuration.
        if image_size.width <= 0 or image_size.height <= 0:
            continue

        indices_x = (
            _visible_repeat_indices(base_position.x, image_size.width, viewport, "x")
            if image_layer.repeat_x
            else (0,)
        )
        indices_y = (
            _visible_repeat_indices(base_position.y, image_size.height, viewport, "y")
            if image_layer.repeat_y
            else (0,)
        )

        # Y varies slowest, X varies fastest so placements for a layer read
        # like rows in a grid, matching typical texture tiling order.
        for index_y in indices_y:
            for index_x in indices_x:
                target_rect = Rect(
                    base_position.x + index_x * image_size.width,
                    base_position.y + index_y * image_size.height,
                    image_size.width,
                    image_size.height,
                )

                clip_rect = _intersection(target_rect, viewport)
                if clip_rect is None:
                    continue

                source_rect = Rect(
                    clip_rect.x - target_rect.x,
                    clip_rect.y - target_rect.y,
                    clip_rect.width,
                    clip_rect.height,
                )

                placements.append(
                    ImageLayerPlacement(
                        layer=image_layer,
                        image=image_layer.image,
                        target_rect=target_rect,
                        viewport_clip_rect=clip_rect,
                        source_rect=source_rect,
                        effective_opacity=effective_opacity,
                        repeat_index_x=index_x,
                        repeat_index_y=index_y,
                    )
                )

    return placements
