"""Parse Tiled Maps and Tilesets

See: https://www.mapeditor.org/

This library is for parsing JSON formatted Tiled Map Editormaps and tilesets to be
    used as maps and levels for 2D top-down (orthogonal, hexogonal, or isometric)
    or side-scrolling games in a strictly typed fashion.

PyTiled Parser is not tied to any particular graphics library or game engine.
"""

# pylint: disable=too-few-public-methods

from .common_types import Color, OrderedPair, Size
from .effective_layers import (
    DecodedGID,
    EffectiveTileLayer,
    decode_gid,
    get_effective_tile_layers,
    iter_effective_tile_layers,
)
from .exception import (
    CyclicLayerGroupError,
    InvalidTintColorError,
    UnknownFormat,
    UnsupportedOrientationError,
)
from .layer import Chunk, ImageLayer, Layer, LayerGroup, ObjectLayer, TileLayer
from .parser import parse_map, parse_tileset, parse_world
from .properties import Properties, Property
from .tiled_map import TiledMap
from .tileset import Frame, Grid, Tile, Tileset, Transformations
from .world import World, WorldMap

__all__ = [
    "Chunk",
    "Color",
    "CyclicLayerGroupError",
    "DecodedGID",
    "EffectiveTileLayer",
    "Frame",
    "Grid",
    "ImageLayer",
    "InvalidTintColorError",
    "Layer",
    "LayerGroup",
    "ObjectLayer",
    "OrderedPair",
    "Properties",
    "Property",
    "Size",
    "Tile",
    "TileLayer",
    "TiledMap",
    "Tileset",
    "Transformations",
    "UnknownFormat",
    "UnsupportedOrientationError",
    "World",
    "WorldMap",
    "decode_gid",
    "get_effective_tile_layers",
    "iter_effective_tile_layers",
    "parse_map",
    "parse_tileset",
    "parse_world",
]
