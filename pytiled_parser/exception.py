class UnknownFormat(Exception):
    pass


class TileCollisionError(Exception):
    """Base class for errors raised while extracting tile collision shapes."""


class UnsupportedOrientationError(TileCollisionError, ValueError):
    """Raised when tile collision extraction is attempted on a non-orthogonal map."""


class InvalidTileGidError(TileCollisionError, ValueError):
    """Raised when a tile GID cannot be resolved to a tileset/tile, or uses
    flip flags that are invalid for the map's orientation."""


class UnsupportedCollisionObjectError(TileCollisionError, ValueError):
    """Raised when a tile collision object cannot be converted to world-space
    geometry (for example, rotated collision objects)."""
