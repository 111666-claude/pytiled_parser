"""Parse Tiled Maps and Tilesets

See: https://www.mapeditor.org/

This library is for parsing JSON formatted Tiled Map Editormaps and tilesets to be
    used as maps and levels for 2D top-down (orthogonal, hexogonal, or isometric)
    or side-scrolling games in a strictly typed fashion.

PyTiled Parser is not tied to any particular graphics library or game engine.
"""

# pylint: disable=too-few-public-methods

from .common_types import Color, OrderedPair, Size
from .exception import (
    InvalidTileGidError,
    TileCollisionError,
    UnknownFormat,
    UnsupportedCollisionObjectError,
    UnsupportedOrientationError,
)
from .layer import Chunk, ImageLayer, Layer, LayerGroup, ObjectLayer, TileLayer
from .parser import parse_map, parse_world, parse_tileset
from .properties import Properties, Property
from .tile_collision import (
    CollisionEllipse,
    CollisionPolygon,
    CollisionRectangle,
    CollisionShape,
    TileCollision,
    extract_tile_collisions,
)
from .tiled_map import TiledMap
from .tileset import Frame, Grid, Tile, Tileset, Transformations
from .world import World, WorldMap

__all__ = [
    "Color",
    "OrderedPair",
    "Size",
    "UnknownFormat",
    "TileCollisionError",
    "UnsupportedOrientationError",
    "InvalidTileGidError",
    "UnsupportedCollisionObjectError",
    "Chunk",
    "ImageLayer",
    "Layer",
    "LayerGroup",
    "ObjectLayer",
    "TileLayer",
    "parse_map",
    "parse_world",
    "parse_tileset",
    "Properties",
    "Property",
    "CollisionEllipse",
    "CollisionPolygon",
    "CollisionRectangle",
    "CollisionShape",
    "TileCollision",
    "extract_tile_collisions",
    "TiledMap",
    "Frame",
    "Grid",
    "Tile",
    "Tileset",
    "Transformations",
    "World",
    "WorldMap",
]
