"""Animation tile lookup for tile layers.

Tiled stores global tile IDs (GIDs) in tile layer data. A GID may have the
horizontal, vertical and diagonal flip flags encoded in its highest bits, and
the tile it points to may itself have an animation made up of
[Frame][pytiled_parser.tileset.Frame] objects. This module provides helpers
for resolving which tile should actually be displayed at a given point in
time, while preserving the flip flags of the cell the animation lives in.

`Tiled Animation Docs <https://doc.mapeditor.org/en/stable/manual/editing-tilesets/#tile-animation-editor>`_
"""

from typing import Dict, Optional, Tuple

from pytiled_parser.common_types import OrderedPair
from pytiled_parser.layer import TileLayer
from pytiled_parser.tileset import Tileset

# Bit masks for the flip flags Tiled packs into the high bits of a GID.
#
# `TMX Reference <https://doc.mapeditor.org/en/stable/reference/tmx-map-format/#tile-flipping>`_
FLIPPED_HORIZONTALLY_FLAG = 0x80000000
FLIPPED_VERTICALLY_FLAG = 0x40000000
FLIPPED_DIAGONALLY_FLAG = 0x20000000

# Everything except the three flip flag bits.
GID_MASK = 0x1FFFFFFF


def strip_flip_flags(gid: int) -> int:
    """Strip the flip flag bits from a GID, returning the plain global tile ID.

    Args:
        gid: A global tile ID as stored in tile layer data, potentially with
            flip flag bits set.

    Returns:
        The GID with the horizontal, vertical and diagonal flip bits removed.
    """
    return gid & GID_MASK


def get_flip_flags(gid: int) -> Tuple[bool, bool, bool]:
    """Extract the flip flags from a GID.

    Args:
        gid: A global tile ID as stored in tile layer data.

    Returns:
        A tuple of ``(flipped_horizontally, flipped_vertically,
        flipped_diagonally)`` booleans.
    """
    return (
        bool(gid & FLIPPED_HORIZONTALLY_FLAG),
        bool(gid & FLIPPED_VERTICALLY_FLAG),
        bool(gid & FLIPPED_DIAGONALLY_FLAG),
    )


def get_tileset_for_gid(tilesets: Dict[int, Tileset], gid: int) -> Optional[Tileset]:
    """Find the tileset that a plain GID belongs to.

    The map's tilesets are keyed by their ``firstgid``. A GID belongs to the
    tileset with the greatest ``firstgid`` that is less than or equal to it,
    provided it does not exceed that tileset's ``tile_count`` range.

    Args:
        tilesets: The ``TiledMap.tilesets`` dict, keyed by firstgid.
        gid: A plain GID with the flip flag bits already removed. Must be
            greater than zero.

    Returns:
        The matching [Tileset][pytiled_parser.tileset.Tileset], or ``None`` if
        no tileset covers the GID.
    """
    matching_tileset: Optional[Tileset] = None
    for firstgid in sorted(tilesets.keys()):
        if firstgid <= gid:
            matching_tileset = tilesets[firstgid]
        else:
            break

    if matching_tileset is not None:
        local_id = gid - matching_tileset.firstgid
        if local_id >= matching_tileset.tile_count:
            return None

    return matching_tileset


def get_cell_gid(layer: TileLayer, x: int, y: int) -> Optional[int]:
    """Get the raw GID stored in a tile layer cell, including flip flags.

    Handles both regular tile layers (``layer.data``) and infinite map layers
    (``layer.chunks``).

    Args:
        layer: The tile layer to read from.
        x: X coordinate of the cell in tiles.
        y: Y coordinate of the cell in tiles.

    Returns:
        The raw GID at the coordinate, ``0`` for an empty cell, or ``None`` if
        the coordinate falls outside of the layer's data or any of its chunks.
    """
    if x < 0 or y < 0:
        return None

    if layer.chunks is not None:
        for chunk in layer.chunks:
            chunk_x = int(chunk.coordinates[0])
            chunk_y = int(chunk.coordinates[1])
            local_x = x - chunk_x
            local_y = y - chunk_y
            if 0 <= local_x < int(chunk.size[0]) and 0 <= local_y < int(chunk.size[1]):
                return chunk.data[local_y][local_x]
        return None

    if layer.data is None:
        return None

    if y >= len(layer.data) or x >= len(layer.data[y]):
        return None

    return layer.data[y][x]


def _resolve_animation(tileset: Tileset, local_id: int, time_ms: int) -> Optional[int]:
    """Resolve the local tile ID displayed by an animated tile at a time.

    Frames with a non-positive duration are ignored, since they can never be
    displayed for a non-negative length of time. If the tile has no usable
    animation the original local ID is returned. Time is wrapped around the
    total animation length so animations loop forever without overflowing.

    Args:
        tileset: The tileset the animated tile belongs to.
        local_id: The local ID of the animated tile within the tileset.
        time_ms: Elapsed time in milliseconds. Must be non-negative.

    Returns:
        The local tile ID of the frame displayed at ``time_ms``, or ``None`` if
        a frame points to a tile ID that does not exist in the tileset.
    """
    tile = tileset.tiles.get(local_id) if tileset.tiles is not None else None
    if tile is None or not tile.animation:
        return local_id

    frames = [
        frame
        for frame in tile.animation
        if frame.duration is not None and frame.duration > 0
    ]
    if not frames:
        return local_id

    total_duration = sum(frame.duration for frame in frames)
    elapsed = time_ms % total_duration

    cumulative = 0
    for frame in frames:
        if elapsed < cumulative + frame.duration:
            if not _tile_id_exists(tileset, frame.tile_id):
                return None
            return frame.tile_id
        cumulative += frame.duration

    # Unreachable given the modulo above; the last frame is a safe fallback.
    return frames[-1].tile_id  # pragma: no cover


def _tile_id_exists(tileset: Tileset, local_id: int) -> bool:
    """Check whether a local tile ID exists within a tileset.

    Image collection tilesets only store explicitly defined tiles in their
    ``tiles`` dict, while spritesheet tilesets implicitly contain every ID in
    the range ``0`` to ``tile_count - 1``.

    Args:
        tileset: The tileset to check.
        local_id: A local tile ID within the tileset.

    Returns:
        ``True`` if the tile ID is valid for the tileset.
    """
    if local_id < 0 or local_id >= tileset.tile_count:
        return False
    if (
        tileset.tiles is not None
        and tileset.tiles
        and local_id not in tileset.tiles
        # An image collection tileset (no single spritesheet image) only has
        # entries for the tiles it explicitly defines.
        and tileset.image is None
    ):
        return False
    return True


def get_tile_gid_at(
    layer: TileLayer,
    tilesets: Dict[int, Tileset],
    coordinates: OrderedPair,
    time_ms: int,
) -> Optional[int]:
    """Get the GID that should be displayed in a layer cell at a point in time.

    Static cells are returned unchanged. If the cell references an animated
    tile, its [Frame][pytiled_parser.tileset.Frame] durations are followed and
    wrapped so the animation loops indefinitely. The flip flags (horizontal,
    vertical and diagonal) of the original cell are preserved on the returned
    GID.

    Works with regular tile layers and infinite map layers made up of chunks,
    and with maps that use multiple tilesets.

    Args:
        layer: The tile layer to query.
        tilesets: The ``TiledMap.tilesets`` dict, keyed by firstgid.
        coordinates: The ``(x, y)`` cell coordinate in tiles.
        time_ms: Elapsed time in milliseconds. Must be non-negative.

    Returns:
        The GID to display at ``time_ms``, including the cell's original flip
        flags. ``0`` is returned for an empty cell and ``None`` for a coordinate
        outside the layer (including negative coordinates), a GID no tileset
        covers, or an animation frame referencing an unknown tile ID.

    Raises:
        ValueError: If ``time_ms`` is negative, or ``coordinates`` does not
            contain integer tile coordinates.
    """
    if time_ms < 0:
        raise ValueError(
            f"time_ms must be a non-negative number of milliseconds, got {time_ms}"
        )

    x, y = coordinates
    if (
        not isinstance(x, int)
        or not isinstance(y, int)
        or isinstance(x, bool)
        or isinstance(y, bool)
    ):
        raise ValueError(
            f"coordinates must be integer tile coordinates, got {coordinates}"
        )

    raw_gid = get_cell_gid(layer, x, y)
    if raw_gid is None:
        return None
    if raw_gid == 0:
        return 0

    plain_gid = strip_flip_flags(raw_gid)
    flip_bits = raw_gid & ~GID_MASK

    tileset = get_tileset_for_gid(tilesets, plain_gid)
    if tileset is None:
        return None

    local_id = plain_gid - tileset.firstgid
    resolved_local_id = _resolve_animation(tileset, local_id, time_ms)
    if resolved_local_id is None:
        return None

    return tileset.firstgid + resolved_local_id + flip_bits
