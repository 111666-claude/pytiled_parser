"""Parse Tiled Maps and Tilesets

See: https://www.mapeditor.org/

This library is for parsing JSON formatted Tiled Map Editormaps and tilesets to be
    used as maps and levels for 2D top-down (orthogonal, hexogonal, or isometric)
    or side-scrolling games in a strictly typed fashion.

PyTiled Parser is not tied to any particular graphics library or game engine.
"""

# pylint: disable=too-few-public-methods

from .common_types import Color, OrderedPair, Rect, Size
from .exception import UnknownFormat
from .gid import (
    FLIP_DIAGONALLY_FLAG,
    FLIP_HORIZONTALLY_FLAG,
    FLIP_VERTICALLY_FLAG,
    GID_MASK,
    GIDTransform,
    TileSource,
    decode_gid,
    find_tilesets,
    get_tile_source,
    get_tile_source_from_tileset,
    resolve_tile,
)
from .gid_exception import (
    GIDError,
    GIDNotFoundError,
    InvalidGIDError,
    TileImageError,
    TileImageNotFoundError,
    TileRectError,
    TilesetOverlapError,
)
from .layer import Chunk, ImageLayer, Layer, LayerGroup, ObjectLayer, TileLayer
from .parser import parse_map, parse_tileset, parse_world
from .properties import Properties, Property
from .tiled_map import TiledMap
from .tileset import Frame, Grid, Tile, Tileset, Transformations
from .world import World, WorldMap

__all__ = [
    "FLIP_DIAGONALLY_FLAG",
    "FLIP_HORIZONTALLY_FLAG",
    "FLIP_VERTICALLY_FLAG",
    "GID_MASK",
    "Chunk",
    "Color",
    "Frame",
    "GIDError",
    "GIDNotFoundError",
    "GIDTransform",
    "Grid",
    "ImageLayer",
    "InvalidGIDError",
    "Layer",
    "LayerGroup",
    "ObjectLayer",
    "OrderedPair",
    "Properties",
    "Property",
    "Rect",
    "Size",
    "Tile",
    "TileImageError",
    "TileImageNotFoundError",
    "TileLayer",
    "TileRectError",
    "TileSource",
    "TiledMap",
    "Tileset",
    "TilesetOverlapError",
    "Transformations",
    "UnknownFormat",
    "World",
    "WorldMap",
    "decode_gid",
    "find_tilesets",
    "get_tile_source",
    "get_tile_source_from_tileset",
    "parse_map",
    "parse_tileset",
    "parse_world",
    "resolve_tile",
]
