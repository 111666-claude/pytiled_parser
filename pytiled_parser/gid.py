"""Tile source (GID) lookup API.

Renderers working with a parsed map receive raw, 32 bit unsigned global tile
IDs (GIDs) from the tile layer data and tile objects. A raw GID does not only
identify a tile: its four most significant bits may carry flip and rotation
flags. Resolving such a GID therefore means:

1. Splitting the flip flags off of the plain tile GID.
2. Finding the :class:`~pytiled_parser.tileset.Tileset` whose ``firstgid``
   range contains the plain GID.
3. Computing the local tile ID inside of that tileset.
4. Determining the image file and the pixel rectangle to crop out of it.
5. Reporting the flips that need to be applied when drawing the tile.

This module performs all of those steps in one place so that every renderer
gets the same, deterministic result. The raw GID passed in by the caller is
never modified; all results report it unchanged.

`Tiled GID and flip flag reference
<https://doc.mapeditor.org/en/stable/reference/global-tile-ids/>`_
"""

from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Tuple, Union

from .common_types import OrderedPair, Rect
from .gid_exception import (
    GIDError,
    GIDNotFoundError,
    InvalidGIDError,
    TileImageError,
    TileImageNotFoundError,
    TileRectError,
    TilesetOverlapError,
)
from .tiled_map import TiledMap
from .tileset import Tileset

# Tiled stores the horizontal flip flag in the most significant bit of the
# raw, unsigned 32 bit GID.
FLIP_HORIZONTALLY_FLAG: int = 0x80000000
# Tiled stores the vertical flip flag in the second most significant bit.
FLIP_VERTICALLY_FLAG: int = 0x40000000
# Tiled stores the diagonal flip (anti-diagonal transpose) flag in the third
# most significant bit. Together with the horizontal and vertical flags this
# encodes the eight possible 90 degree based transformations.
FLIP_DIAGONALLY_FLAG: int = 0x20000000
# Tiled 1.9 added a 120 degree rotation flag used with hexagonal maps. The
# tile source query does not support it, GIDs carrying this flag are rejected.
ROTATED_HEXAGONAL_120_FLAG: int = 0x10000000

# Bit mask which keeps only the plain GID and strips all of the flag bits.
GID_MASK: int = 0x0FFFFFFF

# A raw GID is an unsigned 32 bit integer, values above this do not fit.
MAX_GID: int = 0xFFFFFFFF


class GIDTransform(NamedTuple):
    """The flip flags decoded from a raw GID.

    The three booleans correspond directly to the three Tiled flag bits. All
    eight combinations are valid Tiled output. The diagonal flag describes a
    transpose along the anti-diagonal, it is *not* a simple 90 degree rotation
    by itself; combine the flags as Tiled documents them.

    Attributes:
        flipped_horizontally: True if the horizontal flip flag bit is set.
        flipped_vertically: True if the vertical flip flag bit is set.
        flipped_diagonally: True if the diagonal flip flag bit is set.
    """

    flipped_horizontally: bool
    flipped_vertically: bool
    flipped_diagonally: bool


class TileSource(NamedTuple):
    """Everything a renderer needs to draw one cell of a map.

    Attributes:
        gid: The original, unmodified raw GID the lookup was performed with,
            including any flip flag bits.
        plain_gid: The GID with all flip flag bits removed.
        tileset: The tileset the tile belongs to.
        local_id: The local tile ID within :attr:`tileset`.
        image_path: Path of the image file containing the tile. This is the
            spritesheet image for image based tilesets and the per-tile image
            for image collection tilesets.
        rect: The pixel rectangle to crop out of :attr:`image_path`.
        transform: The horizontal, vertical and diagonal flips decoded from
            the raw GID.
        tile_offset: The drawing offset of the tileset in pixels, or
            ``OrderedPair(0, 0)`` when the tileset does not declare one.
    """

    gid: int
    plain_gid: int
    tileset: Tileset
    local_id: int
    image_path: Path
    rect: Rect
    transform: GIDTransform
    tile_offset: OrderedPair


def decode_gid(gid: int) -> Tuple[int, GIDTransform]:
    """Split a raw GID into its plain GID and flip flags.

    Args:
        gid: The raw, unsigned 32 bit GID as stored in tile layer data or a
            tile object.

    Returns:
        A tuple of the plain GID with all flag bits removed and a
        :class:`GIDTransform` describing the flip flags.

    Raises:
        InvalidGIDError: For negative GIDs, GIDs which do not fit into 32
            bits, GID ``0`` carrying flip flags, or GIDs carrying the
            unsupported hexagonal 120 degree rotation flag.
    """
    if not isinstance(gid, int):
        raise InvalidGIDError(
            f"GID must be an integer, got {type(gid).__name__}: {gid!r}", gid
        )

    if gid < 0:
        raise InvalidGIDError(
            f"GID must be an unsigned 32 bit integer, got negative GID: {gid}",
            gid,
        )

    if gid > MAX_GID:
        raise InvalidGIDError(
            f"GID {gid} does not fit into an unsigned 32 bit integer "
            f"(maximum is {MAX_GID})",
            gid,
        )

    if gid & ROTATED_HEXAGONAL_120_FLAG:
        raise InvalidGIDError(
            f"GID {gid} has the Tiled 1.9 hexagonal 120 degree rotation flag "
            "set, which is not supported by the tile source lookup",
            gid,
        )

    transform = GIDTransform(
        flipped_horizontally=bool(gid & FLIP_HORIZONTALLY_FLAG),
        flipped_vertically=bool(gid & FLIP_VERTICALLY_FLAG),
        flipped_diagonally=bool(gid & FLIP_DIAGONALLY_FLAG),
    )

    plain_gid = gid & GID_MASK

    if plain_gid == 0 and gid != 0:
        raise InvalidGIDError(
            f"GID 0 denotes an empty cell and cannot carry flip flags, " f"got {gid}",
            gid,
        )

    return plain_gid, transform


def find_tilesets(plain_gid: int, tilesets: Dict[int, Tileset]) -> List[Tileset]:
    """Find every tileset whose GID range contains ``plain_gid``.

    Args:
        plain_gid: A GID with all flip flag bits removed.
        tilesets: A mapping of ``firstgid`` to tileset, as stored on
            :attr:`pytiled_parser.tiled_map.TiledMap.tilesets`.

    Returns:
        All tilesets whose ``firstgid`` is less than or equal to
        ``plain_gid`` and whose GID range (``firstgid`` through
        ``firstgid + tile_count - 1``) contains it, sorted by ``firstgid``
        in descending order. An empty list means no tileset covers the GID.
    """
    matches = []
    for tileset in tilesets.values():
        last_gid = tileset.firstgid + tileset.tile_count - 1
        if tileset.firstgid <= plain_gid <= last_gid:
            matches.append(tileset)

    matches.sort(key=lambda ts: ts.firstgid, reverse=True)
    return matches


def _spritesheet_rect(
    tileset: Tileset, local_id: int, gid: int, plain_gid: int
) -> Rect:
    """Compute the crop rectangle for a tile inside a spritesheet image."""
    if tileset.columns <= 0:
        raise TileRectError(
            f"Tileset {tileset.name!r} uses spritesheet image "
            f"{tileset.image} but declares {tileset.columns} columns, "
            "a positive number of columns is required to locate tile "
            f"local ID {local_id} (raw GID {gid})",
            gid,
            plain_gid,
            tileset,
            local_id,
            Rect(0, 0, tileset.tile_width, tileset.tile_height),
        )

    column = local_id % tileset.columns
    row = local_id // tileset.columns

    x = tileset.margin + column * (tileset.tile_width + tileset.spacing)
    y = tileset.margin + row * (tileset.tile_height + tileset.spacing)

    rect = Rect(x, y, tileset.tile_width, tileset.tile_height)

    if (
        tileset.image_width is not None
        and tileset.image_height is not None
        and (
            rect.x + rect.width > tileset.image_width
            or rect.y + rect.height > tileset.image_height
        )
    ):
        raise TileRectError(
            f"Computed crop rectangle {rect} for local tile ID {local_id} "
            f"(raw GID {gid}) falls outside of image {tileset.image} "
            f"which is {tileset.image_width}x{tileset.image_height} pixels; "
            "the tileset image dimensions, margin, spacing, tile size or "
            "tile count do not form a regular grid",
            gid,
            plain_gid,
            tileset,
            local_id,
            rect,
        )

    return rect


def _collection_rect(
    tileset: Tileset, local_id: int, gid: int, plain_gid: int
) -> Tuple[Path, Rect]:
    """Resolve image path and crop rectangle for an image collection tile."""
    if not tileset.tiles or local_id not in tileset.tiles:
        raise TileImageNotFoundError(
            f"Tileset {tileset.name!r} is an image collection but has no "
            f"entry for local tile ID {local_id} (raw GID {gid})",
            gid,
            plain_gid,
            tileset,
            local_id,
        )

    tile = tileset.tiles[local_id]

    if tile.image is None:
        raise TileImageNotFoundError(
            f"Tile with local ID {local_id} (raw GID {gid}) in tileset "
            f"{tileset.name!r} has no image path",
            gid,
            plain_gid,
            tileset,
            local_id,
        )

    rect = Rect(tile.x, tile.y, tile.width, tile.height)
    return tile.image, rect


def resolve_tile(
    tileset: Tileset,
    local_id: int,
    gid: int,
    plain_gid: int,
) -> Tuple[Path, Rect]:
    """Resolve image path and pixel crop rectangle for one local tile ID.

    Works for both spritesheet tilesets (one shared image with a regular
    margin/spacing grid) and image collection tilesets (one image per tile).

    Args:
        tileset: The tileset the local ID belongs to.
        local_id: The local tile ID within ``tileset``.
        gid: The original raw GID, used for error context.
        plain_gid: The flag-free GID, used for error context.

    Returns:
        A tuple of the image path and the crop rectangle within that image.

    Raises:
        TileImageNotFoundError: When the tileset or tile has no usable image.
        TileRectError: When the crop rectangle can not be computed or does
            not fit the declared image dimensions.
    """
    if tileset.image is not None:
        return tileset.image, _spritesheet_rect(tileset, local_id, gid, plain_gid)

    if tileset.tiles:
        return _collection_rect(tileset, local_id, gid, plain_gid)

    raise TileImageNotFoundError(
        f"Tileset {tileset.name!r} has neither a spritesheet image nor "
        f"individual tile images, cannot resolve local tile ID {local_id} "
        f"(raw GID {gid})",
        gid,
        plain_gid,
        tileset,
        local_id,
    )


def get_tile_source(
    gid: int,
    tilesets: Union[TiledMap, Dict[int, Tileset]],
) -> Optional[TileSource]:
    """Resolve a raw GID into the tile source data a renderer needs.

    The raw GID is left untouched. Flip flags are decoded separately and
    returned as part of the :class:`TileSource`. A GID of ``0`` represents an
    empty cell and always resolves to ``None``.

    Args:
        gid: The raw, unsigned 32 bit GID as stored in tile layer data or a
            tile object, including any flip flag bits.
        tilesets: Either the parsed :class:`~pytiled_parser.tiled_map.TiledMap`
            or its ``tilesets`` mapping of ``firstgid`` to tileset.

    Returns:
        A :class:`TileSource` with the tileset, image path, pixel crop
        rectangle, flip flags and tile offset, or ``None`` for GID ``0``.

    Raises:
        InvalidGIDError: For malformed GIDs, see :func:`decode_gid`.
        GIDNotFoundError: When no tileset covers a non-zero GID.
        TilesetOverlapError: When multiple tilesets cover the GID. The
            deterministic result (highest ``firstgid``) is attached to the
            exception as ``selected_tileset``.
        TileImageNotFoundError: When the tile has no usable image.
        TileRectError: When the crop rectangle can not be computed or does
            not fit the declared image dimensions.
    """
    plain_gid, transform = decode_gid(gid)

    if plain_gid == 0:
        # GID 0 is the empty/transparent cell.
        return None

    if isinstance(tilesets, TiledMap):
        tileset_dict = tilesets.tilesets
    else:
        tileset_dict = tilesets

    candidates = find_tilesets(plain_gid, tileset_dict)

    if not candidates:
        raise GIDNotFoundError(
            f"No tileset covers GID {plain_gid} (raw GID {gid}). Known "
            f"tileset firstgids: "
            f"{sorted(tileset_dict.keys())}",
            gid,
            plain_gid,
        )

    tileset = candidates[0]

    if len(candidates) > 1:
        overlap_names = [
            f"{ts.name!r} (firstgid {ts.firstgid}, " f"{ts.tile_count} tiles)"
            for ts in candidates
        ]
        raise TilesetOverlapError(
            f"GID {plain_gid} (raw GID {gid}) is covered by multiple "
            f"overlapping tilesets: {', '.join(overlap_names)}. Using "
            f"{tileset.name!r} with the highest firstgid "
            f"({tileset.firstgid}) as the deterministic result",
            gid,
            plain_gid,
            candidates,
        )

    local_id = plain_gid - tileset.firstgid

    image_path, rect = resolve_tile(tileset, local_id, gid, plain_gid)

    tile_offset = (
        tileset.tile_offset if tileset.tile_offset is not None else OrderedPair(0, 0)
    )

    return TileSource(
        gid=gid,
        plain_gid=plain_gid,
        tileset=tileset,
        local_id=local_id,
        image_path=image_path,
        rect=rect,
        transform=transform,
        tile_offset=tile_offset,
    )


def get_tile_source_from_tileset(gid: int, tileset: Tileset) -> Optional[TileSource]:
    """Resolve a raw GID against a single, known tileset.

    This is the convenience form for callers which already know which
    tileset a GID belongs to (for example when iterating one tileset's GID
    range directly). The GID must fall within the tileset's
    ``firstgid``/``tile_count`` range.

    Args:
        gid: The raw, unsigned 32 bit GID including any flip flag bits.
        tileset: The tileset expected to contain the tile.

    Returns:
        A :class:`TileSource`, or ``None`` for GID ``0``.

    Raises:
        InvalidGIDError: For malformed GIDs, see :func:`decode_gid`.
        GIDNotFoundError: When the GID does not belong to ``tileset``.
        TileImageNotFoundError: When the tile has no usable image.
        TileRectError: When the crop rectangle can not be computed or does
            not fit the declared image dimensions.
    """
    plain_gid, transform = decode_gid(gid)

    if plain_gid == 0:
        return None

    last_gid = tileset.firstgid + tileset.tile_count - 1
    if not tileset.firstgid <= plain_gid <= last_gid:
        raise GIDNotFoundError(
            f"GID {plain_gid} (raw GID {gid}) is not within tileset "
            f"{tileset.name!r} range {tileset.firstgid}-{last_gid}",
            gid,
            plain_gid,
        )

    local_id = plain_gid - tileset.firstgid
    image_path, rect = resolve_tile(tileset, local_id, gid, plain_gid)

    tile_offset = (
        tileset.tile_offset if tileset.tile_offset is not None else OrderedPair(0, 0)
    )

    return TileSource(
        gid=gid,
        plain_gid=plain_gid,
        tileset=tileset,
        local_id=local_id,
        image_path=image_path,
        rect=rect,
        transform=transform,
        tile_offset=tile_offset,
    )


__all__ = [
    "FLIP_DIAGONALLY_FLAG",
    "FLIP_HORIZONTALLY_FLAG",
    "FLIP_VERTICALLY_FLAG",
    "GID_MASK",
    "MAX_GID",
    "ROTATED_HEXAGONAL_120_FLAG",
    "GIDError",
    "GIDNotFoundError",
    "GIDTransform",
    "InvalidGIDError",
    "TileImageError",
    "TileImageNotFoundError",
    "TileRectError",
    "TileSource",
    "TilesetOverlapError",
    "decode_gid",
    "find_tilesets",
    "get_tile_source",
    "get_tile_source_from_tileset",
    "resolve_tile",
]
