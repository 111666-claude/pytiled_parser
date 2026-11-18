"""Parse Tiled Maps and Tilesets

See: https://www.mapeditor.org/

This library is for parsing JSON formatted Tiled Map Editormaps and tilesets to be
    used as maps and levels for 2D top-down (orthogonal, hexogonal, or isometric)
    or side-scrolling games in a strictly typed fashion.

PyTiled Parser is not tied to any particular graphics library or game engine.
"""

# pylint: disable=too-few-public-methods

from .common_types import Color, OrderedPair, Size
from .exception import ObjectTemplateError, UnknownFormat
from .layer import Chunk, ImageLayer, Layer, LayerGroup, ObjectLayer, TileLayer
from .object_ref import ObjectRefError, ObjectRefResolution, resolve_object_references
from .parser import parse_map, parse_world, parse_tileset
from .properties import ObjectRef, Properties, Property
from .tiled_map import TiledMap
from .tileset import Frame, Grid, Tile, Tileset, Transformations
from .world import World, WorldMap

__all__ = [
    "Color",
    "OrderedPair",
    "Size",
    "ObjectTemplateError",
    "UnknownFormat",
    "Chunk",
    "ImageLayer",
    "Layer",
    "LayerGroup",
    "ObjectLayer",
    "TileLayer",
    "ObjectRefError",
    "ObjectRefResolution",
    "resolve_object_references",
    "parse_map",
    "parse_world",
    "parse_tileset",
    "ObjectRef",
    "Properties",
    "Property",
    "TiledMap",
    "Frame",
    "Grid",
    "Tile",
    "Tileset",
    "Transformations",
    "World",
    "WorldMap",
]
