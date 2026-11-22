"""Coordinate conversion between tile coordinates and pixel coordinates.

This module provides the two entry points needed by editors and games that
need to map between the tile grid and the pixel space of a map:

- :func:`tile_to_pixel` converts tile coordinates to the pixel anchor
  (the top-left corner of the tile's bounding box) of that tile.
- :func:`pixel_to_tile` converts a pixel position to the integer tile
  coordinates of the tile hit at that position.

All four map orientations supported by Tiled (``orthogonal``, ``isometric``,
``staggered`` and ``hexagonal``) are handled, using the map's ``tile_size``,
``hex_side_length``, ``stagger_axis`` and ``stagger_index`` attributes. The
math mirrors Tiled's own renderers, so results match what the Tiled editor
displays.

Negative coordinates are fully supported (they occur naturally with infinite
maps) and tile boundaries are deterministic: a pixel exactly on a boundary
resolves to a single well-defined tile. The two conversions are stable
inverses at tile centers, i.e. converting a tile to its anchor, offsetting to
the center of the tile, and converting back always yields the original tile.
"""

import math

from pytiled_parser.common_types import OrderedPair
from pytiled_parser.tiled_map import TiledMap

__all__ = ["pixel_to_tile", "tile_to_pixel"]


class _RenderParams:
    """Derived geometry values shared by the staggered/hexagonal conversions.

    Mirrors the ``RenderParams`` struct of Tiled's HexagonalRenderer, which
    the StaggeredRenderer also uses (with zero side lengths).
    """

    def __init__(self, tiled_map: TiledMap) -> None:
        self.tile_width = tiled_map.tile_size.width
        self.tile_height = tiled_map.tile_size.height
        if self.tile_width <= 0 or self.tile_height <= 0:
            raise ValueError(
                "tile_size must be positive to convert coordinates, got "
                f"{tiled_map.tile_size.width}x{tiled_map.tile_size.height}"
            )

        self.stagger_x = tiled_map.stagger_axis == "x"
        self.stagger_even = tiled_map.stagger_index == "even"

        self.side_length_x = 0.0
        self.side_length_y = 0.0
        if tiled_map.orientation == "hexagonal":
            hex_side_length = tiled_map.hex_side_length or 0
            if self.stagger_x:
                self.side_length_x = hex_side_length
            else:
                self.side_length_y = hex_side_length

        self.side_offset_x = (self.tile_width - self.side_length_x) / 2
        self.side_offset_y = (self.tile_height - self.side_length_y) / 2

        self.column_width = self.side_offset_x + self.side_length_x
        self.row_height = self.side_offset_y + self.side_length_y


def _do_stagger(index: int, stagger_even: bool) -> bool:
    """Whether the given row/column index is shifted."""
    return bool((index & 1) ^ int(stagger_even))


def tile_to_pixel(tiled_map: TiledMap, tile_x: int, tile_y: int) -> OrderedPair:
    """Convert tile coordinates to the pixel anchor of the tile.

    The anchor is the top-left corner of the tile's bounding box in pixel
    space, matching the position Tiled renders the tile image at.

    Args:
        tiled_map: The map whose orientation and sizing to use.
        tile_x: The x coordinate of the tile, in tiles.
        tile_y: The y coordinate of the tile, in tiles.

    Returns:
        The pixel coordinates of the tile's anchor as an :OrderedPair:.

    Raises:
        ValueError: If the map has a non-positive tile size or an unknown
            orientation.
    """
    tile_width = tiled_map.tile_size.width
    tile_height = tiled_map.tile_size.height
    if tile_width <= 0 or tile_height <= 0:
        raise ValueError(
            "tile_size must be positive to convert coordinates, got "
            f"{tile_width}x{tile_height}"
        )

    orientation = tiled_map.orientation

    if orientation == "orthogonal":
        return OrderedPair(tile_x * tile_width, tile_y * tile_height)

    if orientation == "isometric":
        origin_x = tiled_map.map_size.height * tile_width / 2
        return OrderedPair(
            (tile_x - tile_y) * tile_width / 2 + origin_x,
            (tile_x + tile_y) * tile_height / 2,
        )

    if orientation in ("staggered", "hexagonal"):
        params = _RenderParams(tiled_map)
        if params.stagger_x:
            pixel_y = tile_y * (params.tile_height + params.side_length_y)
            if _do_stagger(tile_x, params.stagger_even):
                pixel_y += params.row_height
            pixel_x = tile_x * params.column_width
        else:
            pixel_x = tile_x * (params.tile_width + params.side_length_x)
            if _do_stagger(tile_y, params.stagger_even):
                pixel_x += params.column_width
            pixel_y = tile_y * params.row_height
        return OrderedPair(pixel_x, pixel_y)

    raise ValueError(f"Unknown map orientation: {orientation!r}")


def _orthogonal_pixel_to_tile(
    tiled_map: TiledMap, x: float, y: float
) -> tuple[int, int]:
    return (
        math.floor(x / tiled_map.tile_size.width),
        math.floor(y / tiled_map.tile_size.height),
    )


def _isometric_pixel_to_tile(
    tiled_map: TiledMap, x: float, y: float
) -> tuple[int, int]:
    tile_width = tiled_map.tile_size.width
    tile_height = tiled_map.tile_size.height
    x -= tiled_map.map_size.height * tile_width / 2
    tile_y = y / tile_height
    tile_x = x / tile_width
    return (math.floor(tile_y + tile_x), math.floor(tile_y - tile_x))


def _staggered_neighbor_top_left(
    x: int, y: int, stagger_x: bool, stagger_even: bool
) -> tuple[int, int]:
    if not stagger_x:
        if _do_stagger(y, stagger_even):
            return (x, y - 1)
        return (x - 1, y - 1)
    if _do_stagger(x, stagger_even):
        return (x - 1, y)
    return (x - 1, y - 1)


def _staggered_neighbor_top_right(
    x: int, y: int, stagger_x: bool, stagger_even: bool
) -> tuple[int, int]:
    if not stagger_x:
        if _do_stagger(y, stagger_even):
            return (x + 1, y - 1)
        return (x, y - 1)
    if _do_stagger(x, stagger_even):
        return (x + 1, y)
    return (x + 1, y - 1)


def _staggered_neighbor_bottom_left(
    x: int, y: int, stagger_x: bool, stagger_even: bool
) -> tuple[int, int]:
    if not stagger_x:
        if _do_stagger(y, stagger_even):
            return (x, y + 1)
        return (x - 1, y + 1)
    if _do_stagger(x, stagger_even):
        return (x - 1, y + 1)
    return (x - 1, y)


def _staggered_neighbor_bottom_right(
    x: int, y: int, stagger_x: bool, stagger_even: bool
) -> tuple[int, int]:
    if not stagger_x:
        if _do_stagger(y, stagger_even):
            return (x + 1, y + 1)
        return (x, y + 1)
    if _do_stagger(x, stagger_even):
        return (x + 1, y + 1)
    return (x + 1, y)


def _staggered_pixel_to_tile(
    tiled_map: TiledMap, x: float, y: float
) -> tuple[int, int]:
    params = _RenderParams(tiled_map)

    aligned_x, aligned_y = x, y
    if params.stagger_x:
        aligned_x -= params.side_offset_x if params.stagger_even else 0
    else:
        aligned_y -= params.side_offset_y if params.stagger_even else 0

    # Start with the coordinates of a grid-aligned tile
    ref_x = math.floor(aligned_x / params.tile_width)
    ref_y = math.floor(aligned_y / params.tile_height)

    # Relative x and y position on the base square of the grid-aligned tile
    rel_x = aligned_x - ref_x * params.tile_width
    rel_y = aligned_y - ref_y * params.tile_height

    # Adjust the reference point to the correct tile coordinates
    if params.stagger_x:
        ref_x *= 2
        if params.stagger_even:
            ref_x += 1
    else:
        ref_y *= 2
        if params.stagger_even:
            ref_y += 1

    y_pos = rel_x * (params.tile_height / params.tile_width)

    # Check whether the cursor is in any of the corners (neighboring tiles)
    stagger_x = params.stagger_x
    stagger_even = params.stagger_even
    if params.side_offset_y - y_pos > rel_y:
        ref_x, ref_y = _staggered_neighbor_top_left(
            ref_x, ref_y, stagger_x, stagger_even
        )
    if -params.side_offset_y + y_pos > rel_y:
        ref_x, ref_y = _staggered_neighbor_top_right(
            ref_x, ref_y, stagger_x, stagger_even
        )
    if params.side_offset_y + y_pos < rel_y:
        ref_x, ref_y = _staggered_neighbor_bottom_left(
            ref_x, ref_y, stagger_x, stagger_even
        )
    if params.side_offset_y * 3 - y_pos < rel_y:
        ref_x, ref_y = _staggered_neighbor_bottom_right(
            ref_x, ref_y, stagger_x, stagger_even
        )

    return (ref_x, ref_y)


def _hexagonal_pixel_to_tile(
    tiled_map: TiledMap, x: float, y: float
) -> tuple[int, int]:
    params = _RenderParams(tiled_map)

    if params.stagger_x:
        x -= params.tile_width if params.stagger_even else params.side_offset_x
    else:
        y -= params.tile_height if params.stagger_even else params.side_offset_y

    # Start with the coordinates of a grid-aligned tile
    ref_x = math.floor(x / (params.column_width * 2))
    ref_y = math.floor(y / (params.row_height * 2))

    # Relative x and y position on the base square of the grid-aligned tile
    rel_x = x - ref_x * params.column_width * 2
    rel_y = y - ref_y * params.row_height * 2

    # Adjust the reference point to the correct tile coordinates
    if params.stagger_x:
        ref_x *= 2
        if params.stagger_even:
            ref_x += 1
    else:
        ref_y *= 2
        if params.stagger_even:
            ref_y += 1

    # Determine the nearest hexagon tile by the distance to the center
    if params.stagger_x:
        left = params.side_length_x / 2
        center_x = left + params.column_width
        center_y = params.tile_height / 2
        centers = [
            (left, center_y),
            (center_x, center_y - params.row_height),
            (center_x, center_y + params.row_height),
            (center_x + params.column_width, center_y),
        ]
        offsets = [(0, 0), (1, -1), (1, 0), (2, 0)]
    else:
        top = params.side_length_y / 2
        center_x = params.tile_width / 2
        center_y = top + params.row_height
        centers = [
            (center_x, top),
            (center_x - params.column_width, center_y),
            (center_x + params.column_width, center_y),
            (center_x, center_y + params.row_height),
        ]
        offsets = [(0, 0), (-1, 1), (0, 1), (0, 2)]

    nearest = 0
    min_dist = None
    for i, (cx, cy) in enumerate(centers):
        dist = (cx - rel_x) ** 2 + (cy - rel_y) ** 2
        if min_dist is None or dist < min_dist:
            min_dist = dist
            nearest = i

    offset_x, offset_y = offsets[nearest]
    return (ref_x + offset_x, ref_y + offset_y)


def pixel_to_tile(tiled_map: TiledMap, pixel_x: float, pixel_y: float) -> OrderedPair:
    """Convert a pixel position to the coordinates of the tile hit there.

    The result is the integer tile coordinate of the tile containing the
    given pixel, using the same hit-testing rules as the Tiled editor.
    Positions outside the map (including negative coordinates, as used by
    infinite maps) still return a deterministic tile coordinate.

    Args:
        tiled_map: The map whose orientation and sizing to use.
        pixel_x: The x position, in pixels.
        pixel_y: The y position, in pixels.

    Returns:
        The integer tile coordinates as an :OrderedPair:.

    Raises:
        ValueError: If the map has a non-positive tile size or an unknown
            orientation.
    """
    if tiled_map.tile_size.width <= 0 or tiled_map.tile_size.height <= 0:
        raise ValueError(
            "tile_size must be positive to convert coordinates, got "
            f"{tiled_map.tile_size.width}x{tiled_map.tile_size.height}"
        )

    orientation = tiled_map.orientation

    if orientation == "orthogonal":
        tile = _orthogonal_pixel_to_tile(tiled_map, pixel_x, pixel_y)
    elif orientation == "isometric":
        tile = _isometric_pixel_to_tile(tiled_map, pixel_x, pixel_y)
    elif orientation == "staggered":
        tile = _staggered_pixel_to_tile(tiled_map, pixel_x, pixel_y)
    elif orientation == "hexagonal":
        tile = _hexagonal_pixel_to_tile(tiled_map, pixel_x, pixel_y)
    else:
        raise ValueError(f"Unknown map orientation: {orientation!r}")

    return OrderedPair(tile[0], tile[1])
