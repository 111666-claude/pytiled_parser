"""Effective layer queries.

The parser stores layers exactly as they appear in the map file. Group
layers do not bake their opacity, offset or tint color into their children,
and nothing pre-computes which tile layers actually cover a given pixel.
This module performs that work on demand without modifying any of the
parsed objects.

Tiled draws layers back-to-front in document order, with the children of a
group layer drawn in place of the group itself. A layer (and everything
below it) is skipped when any ancestor is hidden. The effective values of
a tile layer are:

* ``opacity``  -- the product of the opacities of the layer itself and every
  group ancestor.
* ``offset``   -- the sum, in pixels, of the offsets of the layer and its
  group ancestors.
* ``tint_color`` -- the tint colors of the layer and its group ancestors
  multiplied together channel by channel, or ``None`` when no tint applies.

The parsed layer objects are treated as read-only: all composed values are
returned inside new
[EffectiveTileLayer][pytiled_parser.effective_layers.EffectiveTileLayer]
objects.

For more information about group layers, see
[Tiled's Manual](https://doc.mapeditor.org/en/stable/manual/layers/#group-layers)
"""

import math
from typing import List, NamedTuple, Optional, Tuple

import attr

from pytiled_parser.common_types import Color, OrderedPair
from pytiled_parser.exception import (
    CyclicLayerGroupError,
    InvalidTintColorError,
    UnsupportedOrientationError,
)
from pytiled_parser.layer import LayerGroup, TileLayer
from pytiled_parser.tiled_map import TiledMap

# Tile global IDs reserve their top three bits for the flip flags.
#
# TMX Reference:
# https://doc.mapeditor.org/en/stable/reference/tmx-map-format/#tile-flipping
FLIPPED_HORIZONTALLY_FLAG = 0x80000000
FLIPPED_VERTICALLY_FLAG = 0x40000000
FLIPPED_DIAGONALLY_FLAG = 0x20000000

# Mask covering the actual global tile ID once the flip bits are removed.
GID_MASK = 0x1FFFFFFF


class DecodedGID(NamedTuple):
    """Result of splitting the flip flags off a raw global tile ID.

    Attributes:
        gid: The global tile ID with the three flip flag bits removed.
        flipped_horizontally: Whether the tile is flipped horizontally.
        flipped_vertically: Whether the tile is flipped vertically.
        flipped_diagonally: Whether the tile is flipped diagonally (rotated).
    """

    gid: int
    flipped_horizontally: bool
    flipped_vertically: bool
    flipped_diagonally: bool


def decode_gid(raw_gid: int) -> DecodedGID:
    """Split a raw global tile ID into its tile ID and flip flags.

    Tiled stores horizontal, vertical and diagonal flip state in the top
    three bits of the 32 bit cell value. This helper strips those bits so
    the remaining value can be looked up in the map's tilesets.

    Args:
        raw_gid: The raw cell value read from a tile layer or chunk.

    Returns:
        DecodedGID: The tile ID and the three flip booleans.
    """
    return DecodedGID(
        gid=raw_gid & GID_MASK,
        flipped_horizontally=bool(raw_gid & FLIPPED_HORIZONTALLY_FLAG),
        flipped_vertically=bool(raw_gid & FLIPPED_VERTICALLY_FLAG),
        flipped_diagonally=bool(raw_gid & FLIPPED_DIAGONALLY_FLAG),
    )


@attr.s(auto_attribs=True, kw_only=True)
class EffectiveTileLayer:
    """A visible tile layer entry covering a queried pixel.

    These objects are produced by
    [get_effective_tile_layers][pytiled_parser.effective_layers.get_effective_tile_layers]
    and are returned in draw order (back to front): the last entry is drawn
    on top.

    Attributes:
        layer: The original parsed
            [TileLayer][pytiled_parser.layer.TileLayer]. It is never modified.
        name: The name of the layer, copied for convenient access.
        opacity: Effective opacity, the product of the layer's opacity and
            the opacities of all of its group ancestors.
        offset: Effective rendering offset in pixels, the sum of the layer's
            offset and the offsets of all of its group ancestors.
        tint_color: Effective tint color after multiplying together the
            tint colors of the layer and its group ancestors, or ``None``
            when no tint color applies anywhere in the chain.
        gid: The raw global tile ID of the tile covering the queried point,
            including the flip flag bits.
        gid_without_flags: The global tile ID with the flip flag bits
            removed, suitable for tileset lookups.
        flipped_horizontally: Whether the covering tile is flipped
            horizontally.
        flipped_vertically: Whether the covering tile is flipped vertically.
        flipped_diagonally: Whether the covering tile is flipped
            diagonally.
        tile_coordinates: The column and row of the covering tile in the
            layer's tile coordinate space, after the effective offset is
            removed.
    """

    layer: TileLayer
    name: str
    opacity: float
    offset: OrderedPair
    tint_color: Optional[Color]
    gid: int
    gid_without_flags: int
    flipped_horizontally: bool
    flipped_vertically: bool
    flipped_diagonally: bool
    tile_coordinates: OrderedPair


class _EffectiveState(NamedTuple):
    """Composed state carried while descending the layer tree."""

    opacity: float
    offset: OrderedPair
    tint_color: Optional[Color]


def _validate_tint(tint_color: Optional[Color], layer_name: str) -> None:
    if tint_color is None:
        return
    for channel_name, value in zip(("red", "green", "blue", "alpha"), tint_color):
        if not isinstance(value, int) or value < 0 or value > 255:
            raise InvalidTintColorError(
                f"Layer '{layer_name}' has an invalid tint color channel "
                f"{channel_name}={value}. Each channel must be an integer "
                "between 0 and 255 inclusive."
            )


def _compose_tint(
    parent_tint: Optional[Color], layer_tint: Optional[Color]
) -> Optional[Color]:
    """Multiply two tint colors channel by channel.

    Tint multiplication follows Tiled's behavior: each color channel of the
    child graphics is multiplied by the tint channel and normalized back to
    the 0-255 range. The alpha channels are multiplied the same way, which
    is how a translucent tint on a group reduces the effective opacity of
    its children's colors.
    """
    if parent_tint is None:
        return layer_tint
    if layer_tint is None:
        return parent_tint

    return Color(
        red=(parent_tint.red * layer_tint.red) // 255,
        green=(parent_tint.green * layer_tint.green) // 255,
        blue=(parent_tint.blue * layer_tint.blue) // 255,
        alpha=(parent_tint.alpha * layer_tint.alpha) // 255,
    )


def _compose_state(state: _EffectiveState, layer) -> _EffectiveState:  # type: ignore[no-untyped-def]
    _validate_tint(layer.tint_color, layer.name)
    return _EffectiveState(
        opacity=state.opacity * layer.opacity,
        offset=OrderedPair(
            state.offset.x + layer.offset.x,
            state.offset.y + layer.offset.y,
        ),
        tint_color=_compose_tint(state.tint_color, layer.tint_color),
    )


def _iter_visible_tile_layers(
    layers,  # type: ignore[no-untyped-def]
    state: _EffectiveState,
    ancestor_ids: set,
) -> List[Tuple[TileLayer, _EffectiveState]]:
    """Recursively collect visible tile layers with their effective state.

    Draw order is preserved by walking the layer lists in order. Hidden
    layers prune their entire subtree. Only
    [TileLayer][pytiled_parser.layer.TileLayer] objects are collected;
    object and image layers are not tile layers.
    """
    results: List[Tuple[TileLayer, _EffectiveState]] = []

    for layer in layers:
        if not layer.visible:
            # A hidden ancestor hides every descendant as well.
            continue

        if isinstance(layer, LayerGroup):
            if not layer.layers:
                continue

            layer_id = id(layer)
            if layer_id in ancestor_ids:
                raise CyclicLayerGroupError(
                    f"Layer group '{layer.name}' contains itself (directly "
                    "or indirectly). Effective layer traversal requires an "
                    "acyclic layer tree."
                )

            ancestor_ids.add(layer_id)
            results.extend(
                _iter_visible_tile_layers(
                    layer.layers,
                    _compose_state(state, layer),
                    ancestor_ids,
                )
            )
            ancestor_ids.discard(layer_id)

        elif isinstance(layer, TileLayer):
            results.append((layer, _compose_state(state, layer)))

    return results


def _gid_for_cell(tile_layer: TileLayer, column: int, row: int) -> Optional[int]:
    """Return the raw cell value at a tile coordinate, or None if empty.

    Handles both fixed size grids (``TileLayer.data``) and infinite map
    chunks (``TileLayer.chunks``). A cell value of zero means the cell is
    empty and is reported as None.
    """
    origin_x = int(tile_layer.coordinates.x)
    origin_y = int(tile_layer.coordinates.y)

    if tile_layer.chunks is not None:
        for chunk in tile_layer.chunks:
            chunk_x = int(chunk.coordinates.x)
            chunk_y = int(chunk.coordinates.y)
            local_x = column - chunk_x
            local_y = row - chunk_y
            if 0 <= local_x < int(chunk.size.width) and 0 <= local_y < int(
                chunk.size.height
            ):
                raw_gid = chunk.data[local_y][local_x]
                return raw_gid if raw_gid != 0 else None
        return None

    if tile_layer.data is None or tile_layer.size is None:
        return None

    local_x = column - origin_x
    local_y = row - origin_y
    if not (
        0 <= local_x < int(tile_layer.size.width)
        and 0 <= local_y < int(tile_layer.size.height)
    ):
        return None

    raw_gid = tile_layer.data[local_y][local_x]
    if raw_gid == 0:
        return None
    return raw_gid


def iter_effective_tile_layers(
    layers: List,
) -> List[Tuple[TileLayer, float, OrderedPair, Optional[Color]]]:
    """Flatten a layer tree into visible tile layers with effective values.

    Like
    [get_effective_tile_layers][pytiled_parser.effective_layers.get_effective_tile_layers]
    but without a point query: every visible tile layer in the tree is
    returned in draw order, each paired with its effective opacity, offset
    and tint color. The input layers are not modified.

    Args:
        layers: A list of layers, such as ``TiledMap.layers`` or a
            [LayerGroup's][pytiled_parser.layer.LayerGroup] ``layers``.

    Returns:
        A list of ``(tile_layer, opacity, offset, tint_color)`` tuples in
        draw order, with group properties recursively composed.

    Raises:
        CyclicLayerGroupError: If a group layer is reachable from itself.
        InvalidTintColorError: If a layer carries an out of range tint
            color.
    """
    root_state = _EffectiveState(
        opacity=1.0,
        offset=OrderedPair(0.0, 0.0),
        tint_color=None,
    )
    collected = _iter_visible_tile_layers(layers, root_state, set())
    return [
        (tile_layer, state.opacity, state.offset, state.tint_color)
        for tile_layer, state in collected
    ]


def get_effective_tile_layers(
    tiled_map: TiledMap, point: OrderedPair
) -> List[EffectiveTileLayer]:
    """Return the visible tile layers covering a pixel, in draw order.

    For each visible
    [TileLayer][pytiled_parser.layer.TileLayer] that has a non-empty tile
    cell under ``point``, an
    [EffectiveTileLayer][pytiled_parser.effective_layers.EffectiveTileLayer]
    is returned with the layer's effective opacity, offset and tint color.
    Group layer properties are composed recursively. Entries are ordered
    back to front, so the final entry is the topmost layer at that point.

    Only orthogonal maps are supported. The queried cell is computed with
    floor division after subtracting the effective offset::

        column = floor((point.x - effective_offset.x) / tile_width)
        row    = floor((point.y - effective_offset.y) / tile_height)

    Floor division gives integer cells for negative coordinates and for
    non-integer offsets: a fractional offset shifts which cell a point
    lands in rather than raising an error.

    Args:
        tiled_map: The parsed map to query.
        point: The pixel coordinate to query, as an
            [OrderedPair][pytiled_parser.common_types.OrderedPair] (or any
            two value sequence of ``(x, y)``). Negative coordinates are
            valid and simply miss every layer whose cells start at zero.

    Returns:
        The covering effective tile layer entries in draw order. An empty
        list means no visible tile layer has a tile at that point, which
        also covers queries outside every layer's bounds and maps with no
        visible tile layers.

    Raises:
        UnsupportedOrientationError: If the map is not orthogonal.
        CyclicLayerGroupError: If a group layer is reachable from itself.
        InvalidTintColorError: If a layer carries an out of range tint
            color.
    """
    if tiled_map.orientation != "orthogonal":
        raise UnsupportedOrientationError(
            "Effective tile layer queries currently only support orthogonal "
            f"maps, this map's orientation is '{tiled_map.orientation}'."
        )

    point_x, point_y = point
    tile_width = tiled_map.tile_size.width
    tile_height = tiled_map.tile_size.height

    root_state = _EffectiveState(
        opacity=1.0,
        offset=OrderedPair(0.0, 0.0),
        tint_color=None,
    )
    visible_layers = _iter_visible_tile_layers(tiled_map.layers, root_state, set())

    effective_layers: List[EffectiveTileLayer] = []
    for tile_layer, state in visible_layers:
        column = math.floor((point_x - state.offset.x) / tile_width)
        row = math.floor((point_y - state.offset.y) / tile_height)

        raw_gid = _gid_for_cell(tile_layer, column, row)
        if raw_gid is None:
            # Empty cell, or the point falls outside this layer's data.
            continue

        decoded = decode_gid(raw_gid)
        effective_layers.append(
            EffectiveTileLayer(
                layer=tile_layer,
                name=tile_layer.name,
                opacity=state.opacity,
                offset=state.offset,
                tint_color=state.tint_color,
                gid=raw_gid,
                gid_without_flags=decoded.gid,
                flipped_horizontally=decoded.flipped_horizontally,
                flipped_vertically=decoded.flipped_vertically,
                flipped_diagonally=decoded.flipped_diagonally,
                tile_coordinates=OrderedPair(column, row),
            )
        )

    return effective_layers
