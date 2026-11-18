"""Object hit testing (point selection) for parsed Tiled maps.

This module provides :func:`hit_test`, which answers the editor-style
question: "given a map coordinate, which visible objects are underneath it?".

All five shape types supported by Tiled are handled: :class:`~pytiled_parser.tiled_object.Rectangle`,
:class:`~pytiled_parser.tiled_object.Ellipse`, :class:`~pytiled_parser.tiled_object.Polygon`,
:class:`~pytiled_parser.tiled_object.Polyline` and :class:`~pytiled_parser.tiled_object.Point`.
:class:`~pytiled_parser.tiled_object.Text` and :class:`~pytiled_parser.tiled_object.Tile`
objects are tested against their bounding rectangle, the same way Tiled selects them.

Results are returned in draw order, topmost object first. Layer and group layer
offsets are accumulated when mapping the queried map coordinate into each
object layer's local coordinate space, object rotation is taken into account
for every shape, and invisible objects/layers/groups are never reported.
"""

import math
from typing import Any, List, NamedTuple, Optional, Sequence, Tuple

from pytiled_parser.common_types import OrderedPair
from pytiled_parser.layer import LayerGroup, ObjectLayer
from pytiled_parser.tiled_map import TiledMap
from pytiled_parser.tiled_object import (
    Ellipse,
    Point,
    Polygon,
    Polyline,
    TiledObject,
)

# Floating point slack used so that points lying exactly on a shape boundary
# (boundary tangency) are reliably reported as hits.
_EPSILON = 1e-9


class HitResult(NamedTuple):
    """A single object matched by :func:`hit_test`.

    Attributes:
        layer: The :class:`~pytiled_parser.layer.ObjectLayer` that owns the
            matched object (the innermost layer when groups are nested).
        tiled_object: The matched :class:`~pytiled_parser.tiled_object.TiledObject`.
        object_id: The ID of the matched object, or ``None`` when the object
            has no ID (the parser always supplies one, but manually built or
            partially loaded objects may not).
    """

    layer: ObjectLayer
    tiled_object: TiledObject
    object_id: Optional[int]


def _validate_tolerance(tolerance: Any, name: str) -> float:
    """Validate a click tolerance argument.

    Args:
        tolerance: The candidate tolerance value.
        name: The argument name, used in error messages.

    Returns:
        The tolerance as a float.

    Raises:
        TypeError: If the value is not a real number (booleans are rejected).
        ValueError: If the value is negative, NaN or infinite.
    """
    if not isinstance(tolerance, (int, float)) or isinstance(tolerance, bool):
        raise TypeError(
            f"{name} must be a non-negative number, " f"got {type(tolerance).__name__}"
        )

    tolerance = float(tolerance)
    if math.isnan(tolerance) or math.isinf(tolerance) or tolerance < 0:
        raise ValueError(
            f"{name} must be a finite, non-negative number, got {tolerance}"
        )

    return tolerance


def _validate_point(point: Any) -> OrderedPair:
    """Validate the queried coordinate."""
    if isinstance(point, str) or not isinstance(point, Sequence):
        raise TypeError(
            "point must be a pair of x, y coordinates, e.g. OrderedPair(x, y)"
        )

    if len(point) != 2:
        raise TypeError("point must contain exactly two coordinates (x, y)")

    x, y = point
    if (
        not isinstance(x, (int, float))
        or isinstance(x, bool)
        or not isinstance(y, (int, float))
        or isinstance(y, bool)
    ):
        raise TypeError("point coordinates must be numbers")

    if math.isnan(x) or math.isnan(y) or math.isinf(x) or math.isinf(y):
        raise ValueError("point coordinates must be finite")

    return OrderedPair(float(x), float(y))


def _to_local(point: OrderedPair, tiled_object: TiledObject) -> OrderedPair:
    """Translate a map coordinate into the object's unrotated local space.

    Tiled rotates objects clockwise around their origin
    (``tiled_object.coordinates``). The query point is shifted to the object
    origin and rotated back by the object's rotation so shape tests can run
    against the axis-aligned, unrotated geometry stored on the object.
    """
    delta_x = point.x - tiled_object.coordinates.x
    delta_y = point.y - tiled_object.coordinates.y
    theta = math.radians(tiled_object.rotation)
    cosine = math.cos(theta)
    sine = math.sin(theta)

    return OrderedPair(
        cosine * delta_x + sine * delta_y,
        -sine * delta_x + cosine * delta_y,
    )


def _distance_to_segment(
    point: OrderedPair,
    start: OrderedPair,
    end: OrderedPair,
) -> float:
    """Shortest Euclidean distance from a point to a line segment."""
    segment_x = end.x - start.x
    segment_y = end.y - start.y
    length_squared = segment_x * segment_x + segment_y * segment_y

    if length_squared == 0:
        return math.hypot(point.x - start.x, point.y - start.y)

    projection = (
        (point.x - start.x) * segment_x + (point.y - start.y) * segment_y
    ) / length_squared
    projection = max(0.0, min(1.0, projection))

    closest_x = start.x + projection * segment_x
    closest_y = start.y + projection * segment_y
    return math.hypot(point.x - closest_x, point.y - closest_y)


def _distance_to_polyline(point: OrderedPair, points: List[OrderedPair]) -> float:
    """Shortest distance from a point to any segment of a polyline."""
    if not points:
        return math.inf

    if len(points) == 1:
        return math.hypot(point.x - points[0].x, point.y - points[0].y)

    return min(
        _distance_to_segment(point, points[index], points[index + 1])
        for index in range(len(points) - 1)
    )


def _polygon_area(points: List[OrderedPair]) -> float:
    """Absolute area of a polygon via the shoelace formula."""
    if len(points) < 3:
        return 0.0

    doubled_area = 0.0
    for index in range(len(points)):
        start = points[index]
        end = points[(index + 1) % len(points)]
        doubled_area += start.x * end.y - end.x * start.y

    return abs(doubled_area) / 2.0


def _point_in_polygon(point: OrderedPair, points: List[OrderedPair]) -> bool:
    """Even-odd ray casting test for a point inside a simple polygon."""
    inside = False
    point_count = len(points)

    for index in range(point_count):
        start = points[index]
        end = points[(index + 1) % point_count]

        if (start.y > point.y) != (end.y > point.y):
            cross_x = start.x + (point.y - start.y) * (end.x - start.x) / (
                end.y - start.y
            )
            if cross_x > point.x:
                inside = not inside

    return inside


def _on_polygon_boundary(point: OrderedPair, points: List[OrderedPair]) -> bool:
    """Whether the point lies on the polygon's outline (within epsilon)."""
    point_count = len(points)
    for index in range(point_count):
        start = points[index]
        end = points[(index + 1) % point_count]
        if _distance_to_segment(point, start, end) <= _EPSILON:
            return True

    return False


def _hits_rectangle(
    tiled_object: TiledObject,
    point: OrderedPair,
    line_tolerance: float,
    point_tolerance: float,
) -> bool:
    """Hit test for rectangle-like objects (Rectangle, Text, Tile)."""
    local = _to_local(point, tiled_object)
    width, height = tiled_object.size

    if width == 0 and height == 0:
        # Degenerate zero-size rectangle: behaves like a point object.
        return math.hypot(local.x, local.y) <= point_tolerance + _EPSILON

    if width == 0 or height == 0:
        # Degenerate one-dimensional rectangle: behaves like a segment.
        segment = (
            [OrderedPair(0, 0), OrderedPair(0, height)]
            if width == 0
            else [OrderedPair(0, 0), OrderedPair(width, 0)]
        )
        return (
            _distance_to_segment(local, segment[0], segment[1])
            <= line_tolerance + _EPSILON
        )

    x_min = min(0.0, width)
    x_max = max(0.0, width)
    y_min = min(0.0, height)
    y_max = max(0.0, height)

    return (
        x_min - _EPSILON <= local.x <= x_max + _EPSILON
        and y_min - _EPSILON <= local.y <= y_max + _EPSILON
    )


def _hits_ellipse(
    tiled_object: Ellipse,
    point: OrderedPair,
    line_tolerance: float,
    point_tolerance: float,
) -> bool:
    """Hit test for ellipse objects, including degenerate ones."""
    local = _to_local(point, tiled_object)
    width, height = tiled_object.size

    if width == 0 and height == 0:
        # Degenerate zero-size ellipse: behaves like a point object.
        return math.hypot(local.x, local.y) <= point_tolerance + _EPSILON

    if width == 0 or height == 0:
        # Degenerate one-dimensional ellipse: collapses to a segment.
        segment = (
            [OrderedPair(0, 0), OrderedPair(0, height)]
            if width == 0
            else [OrderedPair(0, 0), OrderedPair(width, 0)]
        )
        return (
            _distance_to_segment(local, segment[0], segment[1])
            <= line_tolerance + _EPSILON
        )

    radius_x = width / 2.0
    radius_y = height / 2.0
    # The ellipse fills the object's box from (0, 0) to (width, height), so
    # its center is at (radius_x, radius_y) in local coordinates.
    normalized_x = local.x / radius_x - 1.0
    normalized_y = local.y / radius_y - 1.0
    normalized_value = normalized_x * normalized_x + normalized_y * normalized_y

    return normalized_value <= 1.0 + _EPSILON


def _hits_polygon(
    tiled_object: Polygon,
    point: OrderedPair,
    line_tolerance: float,
    point_tolerance: float,
) -> bool:
    """Hit test for polygon objects."""
    local = _to_local(point, tiled_object)
    points = tiled_object.points

    if not points:
        return False

    if len(points) == 1:
        # Degenerate single-vertex polygon: behaves like a point object.
        return (
            math.hypot(local.x - points[0].x, local.y - points[0].y)
            <= point_tolerance + _EPSILON
        )

    if _polygon_area(points) <= _EPSILON:
        # Degenerate (collinear) polygon: only its outline can be hit.
        outline = list(points) + [points[0]]
        return _distance_to_polyline(local, outline) <= line_tolerance + _EPSILON

    if _on_polygon_boundary(local, points):
        return True

    return _point_in_polygon(local, points)


def _hits_polyline(
    tiled_object: Polyline,
    point: OrderedPair,
    line_tolerance: float,
    point_tolerance: float,
) -> bool:
    """Hit test for polyline objects."""
    local = _to_local(point, tiled_object)
    points = tiled_object.points

    if not points:
        return False

    if len(points) == 1:
        # Degenerate single-vertex polyline: behaves like a point object.
        return (
            math.hypot(local.x - points[0].x, local.y - points[0].y)
            <= point_tolerance + _EPSILON
        )

    return _distance_to_polyline(local, points) <= line_tolerance + _EPSILON


def _hits_point(
    tiled_object: Point,
    point: OrderedPair,
    point_tolerance: float,
) -> bool:
    """Hit test for point objects.

    A point has no extent to rotate, so the rotation reduces to measuring the
    distance from the query point to the object's coordinate.
    """
    local = _to_local(point, tiled_object)
    return math.hypot(local.x, local.y) <= point_tolerance + _EPSILON


def _object_is_hit(
    tiled_object: TiledObject,
    point: OrderedPair,
    line_tolerance: float,
    point_tolerance: float,
) -> bool:
    """Dispatch a hit test to the correct shape implementation."""
    if not tiled_object.visible:
        return False

    if isinstance(tiled_object, Point):
        return _hits_point(tiled_object, point, point_tolerance)

    if isinstance(tiled_object, Polygon):
        return _hits_polygon(tiled_object, point, line_tolerance, point_tolerance)

    if isinstance(tiled_object, Polyline):
        return _hits_polyline(tiled_object, point, line_tolerance, point_tolerance)

    if isinstance(tiled_object, Ellipse):
        return _hits_ellipse(tiled_object, point, line_tolerance, point_tolerance)

    # Rectangle, Text and Tile are all selected via their bounding rectangle.
    return _hits_rectangle(tiled_object, point, line_tolerance, point_tolerance)


def _iter_object_layers(
    layers: List[Any],
    accumulated_offset: OrderedPair,
    parent_visible: bool,
) -> List[Tuple[ObjectLayer, OrderedPair, bool]]:
    """Flatten the layer tree into object layers with accumulated offsets.

    Group layer offsets and object layer offsets add up, and visibility is
    inherited from every ancestor group. Tile and image layers are irrelevant
    to object selection and skipped.
    """
    flattened: List[Tuple[ObjectLayer, OrderedPair, bool]] = []

    for layer in layers:
        visible = parent_visible and layer.visible
        layer_offset = OrderedPair(
            accumulated_offset.x + layer.offset.x,
            accumulated_offset.y + layer.offset.y,
        )

        if isinstance(layer, LayerGroup):
            child_layers = layer.layers or []
            flattened.extend(_iter_object_layers(child_layers, layer_offset, visible))
        elif isinstance(layer, ObjectLayer):
            flattened.append((layer, layer_offset, visible))

    return flattened


def _topdown_sort_key(tiled_object: TiledObject) -> float:
    """Tiled's "top down" draw order key: increasing object y.

    Tiled paints objects with a smaller y first and objects with a larger y
    last (on top), like characters in a top-down game. The sort is stable, so
    objects sharing the same y keep their file order - object IDs are not
    involved, matching Tiled itself.
    """
    return tiled_object.coordinates.y


def hit_test(
    tiled_map: TiledMap,
    point: Any,
    line_tolerance: float = 0.0,
    point_tolerance: float = 0.0,
) -> List[HitResult]:
    """Return the visible objects at a map coordinate, topmost first.

    Simulates clicking in the Tiled Editor: every visible object whose shape
    contains ``point`` is returned, ordered by draw order from the topmost
    object to the bottommost one.

    Args:
        tiled_map: The parsed map to query.
        point: The map coordinate to test, as an
            :class:`~pytiled_parser.common_types.OrderedPair` or any two
            element sequence of numbers (x, y). Negative coordinates are
            supported.
        line_tolerance: Extra click tolerance in pixels applied to
            :class:`~pytiled_parser.tiled_object.Polyline` objects and the
            outlines of degenerate (collinear) polygons and one-dimensional
            rectangles/ellipses. Must be a finite, non-negative number.
        point_tolerance: Extra click tolerance in pixels applied to
            :class:`~pytiled_parser.tiled_object.Point` objects, zero-size
            rectangles/ellipses and single-vertex polygons/polylines. Must be
            a finite, non-negative number.

    Returns:
        A list of :class:`HitResult`, empty when nothing is hit. Each result
        carries the owning :class:`~pytiled_parser.layer.ObjectLayer`, the
        matched object and its ID (which may be ``None``).

    Raises:
        TypeError: If ``point`` is not a pair of numeric coordinates or a
            tolerance is not a real number.
        ValueError: If either tolerance is negative, NaN or infinite, or the
            point contains non-finite coordinates.
    """
    line_tolerance = _validate_tolerance(line_tolerance, "line_tolerance")
    point_tolerance = _validate_tolerance(point_tolerance, "point_tolerance")
    query_point = _validate_point(point)

    results: List[HitResult] = []

    object_layers = _iter_object_layers(tiled_map.layers, OrderedPair(0.0, 0.0), True)

    # Layers later in the layer list are drawn on top, so walk backwards.
    for layer, layer_offset, visible in reversed(object_layers):
        if not visible:
            continue

        # Shift the query into the layer's offset coordinate space.
        local_point = OrderedPair(
            query_point.x - layer_offset.x,
            query_point.y - layer_offset.y,
        )

        # Build the bottom-to-top draw sequence. In "top down" mode Tiled
        # paints increasing y on top of smaller y; the stable sort keeps the
        # file order on a y tie. In "manual" mode the file order itself is the
        # draw order, last object painted last (on top).
        tiled_objects = list(layer.tiled_objects)
        if layer.draw_order != "manual":
            tiled_objects.sort(key=_topdown_sort_key)

        # Reverse so the topmost object is reported first.
        for tiled_object in reversed(tiled_objects):
            if _object_is_hit(
                tiled_object,
                local_point,
                line_tolerance,
                point_tolerance,
            ):
                results.append(
                    HitResult(
                        layer=layer,
                        tiled_object=tiled_object,
                        object_id=tiled_object.id,
                    )
                )

    return results
