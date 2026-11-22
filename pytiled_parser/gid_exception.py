"""Exceptions raised by the tile source query API in :mod:`pytiled_parser.gid`.

All of the exceptions in this module inherit from :class:`GIDError`, so callers
which do not need to distinguish the individual failure modes can catch that
single base class. Every exception keeps the values that describe the failing
lookup as attributes, allowing the failure to be handled programmatically
without having to parse an error message.
"""

from typing import List

from .common_types import Rect
from .tileset import Tileset


class GIDError(Exception):
    """Base class for every error raised while resolving a raw GID.

    Attributes:
        gid: The original, unmodified GID the failing lookup was performed
            with, including any flip flag bits.
    """

    def __init__(self, message: str, gid: int) -> None:
        super().__init__(message)
        self.gid = gid


class InvalidGIDError(GIDError):
    """Raised when a GID itself is not a valid Tiled GID.

    This covers negative values, values which do not fit into an unsigned
    32 bit integer, GID ``0`` with flip flags set (an empty cell can not be
    flipped), and GIDs carrying the Tiled 1.9 hexagonal 120 degree rotation
    flag, which the tile source query does not support.

    Attributes:
        gid: The original, unmodified GID.
    """


class GIDNotFoundError(GIDError):
    """Raised when a non-zero GID does not belong to any known tileset.

    Attributes:
        gid: The original, unmodified GID.
        plain_gid: The GID with the flip flag bits removed.
    """

    def __init__(self, message: str, gid: int, plain_gid: int) -> None:
        super().__init__(message, gid)
        self.plain_gid = plain_gid


class TilesetOverlapError(GIDError):
    """Raised when more than one tileset covers the same GID range.

    Tiled tilesets are expected not to overlap, but manually edited map files
    may produce overlapping ``firstgid`` ranges. The lookup still has a
    deterministic result, available as :attr:`selected_tileset`: the matching
    tileset with the highest ``firstgid`` (the one Tiled itself would use).

    Attributes:
        gid: The original, unmodified GID.
        plain_gid: The GID with the flip flag bits removed.
        candidates: Every tileset whose GID range contains the GID, sorted by
            ``firstgid`` in descending order.
        selected_tileset: The tileset the deterministic lookup chose, i.e. the
            first entry of :attr:`candidates`.
    """

    def __init__(
        self,
        message: str,
        gid: int,
        plain_gid: int,
        candidates: List[Tileset],
    ) -> None:
        super().__init__(message, gid)
        self.plain_gid = plain_gid
        self.candidates = candidates
        self.selected_tileset = candidates[0]


class TileImageError(GIDError):
    """Base class for tileset data problems encountered while resolving a GID.

    Attributes:
        gid: The original, unmodified GID.
        plain_gid: The GID with the flip flag bits removed.
        tileset: The tileset the GID was resolved against.
        local_id: The local tile ID within that tileset.
    """

    def __init__(
        self,
        message: str,
        gid: int,
        plain_gid: int,
        tileset: Tileset,
        local_id: int,
    ) -> None:
        super().__init__(message, gid)
        self.plain_gid = plain_gid
        self.tileset = tileset
        self.local_id = local_id


class TileImageNotFoundError(TileImageError):
    """Raised when no image can be found for the tile a GID points at.

    This covers a tileset without either a spritesheet image or individual
    tile images, an image collection tileset missing the local tile entry,
    and an individual image tile without an image path.

    Attributes:
        gid: The original, unmodified GID.
        plain_gid: The GID with the flip flag bits removed.
        tileset: The tileset the GID was resolved against.
        local_id: The local tile ID within that tileset.
    """


class TileRectError(TileImageError):
    """Raised when the computed crop rectangle does not fit the image.

    This covers spritesheet tilesets declaring zero or negative ``columns``
    as well as "ragged" images whose dimensions are not a whole number of
    tiles, when the computed rectangle falls outside of the declared image
    width and height.

    Attributes:
        gid: The original, unmodified GID.
        plain_gid: The GID with the flip flag bits removed.
        tileset: The tileset the GID was resolved against.
        local_id: The local tile ID within that tileset.
        rect: The crop rectangle that was computed before the bounds check
            failed.
    """

    def __init__(
        self,
        message: str,
        gid: int,
        plain_gid: int,
        tileset: Tileset,
        local_id: int,
        rect: Rect,
    ) -> None:
        super().__init__(message, gid, plain_gid, tileset, local_id)
        self.rect = rect
