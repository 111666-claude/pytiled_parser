"""Extract world-space collision geometry from the tile layers of orthogonal maps.

Tile collision shapes are drawn in Tiled's tile collision editor and stored on the
tileset (see [Tile.objects][pytiled_parser.tileset.Tile]). This module walks every
[TileLayer][pytiled_parser.layer.TileLayer] of a map -- including layers nested in
[LayerGroups][pytiled_parser.layer.LayerGroup] and infinite maps made of
[Chunks][pytiled_parser.layer.Chunk] -- resolves each GID to its tileset tile, and
reprojects the tile's collision objects into world pixel coordinates.

Layer and group offsets are accumulated, and the GID flip flags (horizontal,
vertical, and diagonal) are applied to the collision geometry. Rectangle, ellipse,
and polygon objects are supported; other object types (point, polyline, text, and
tile objects) inside a tile's object group are skipped.

Only orthogonal maps are supported. See
[extract_tile_collisions][pytiled_parser.tile_collision.extract_tile_collisions]
for the exact behavior around empty GIDs, unknown tilesets, and invalid flip flags.
"""

# pylint: disable=too-few-public-methods

from typing import Iterator, List, NamedTuple, Optional, Tuple, Union

import attr

from pytiled_parser import tiled_object as tiled_object_
from pytiled_parser.common_types import OrderedPair
from pytiled_parser.exception import (
    InvalidTileGidError,
    UnsupportedCollisionObjectError,
    UnsupportedOrientationError,
)
from pytiled_parser.layer import Layer, LayerGroup, TileLayer
from pytiled_parser.tiled_map import TiledMap
from pytiled_parser.tileset import Tile, Tileset

# GID flags as defined by the TMX/JSON map formats.
FLIPPED_HORIZONTALLY_FLAG = 0x80000000
FLIPPED_VERTICALLY_FLAG = 0x40000000
FLIPPED_DIAGONALLY_FLAG = 0x20000000
ROTATED_HEXAGONAL_120_FLAG = 0x10000000

# Mask to strip all flag bits from a raw GID.
GID_MASK = 0x0FFFFFFF


class CollisionRectangle(NamedTuple):
    """Axis-aligned rectangle in world pixel coordinates.

    Attributes:
        x: Left edge of the rectangle in world pixels.
        y: Top edge of the rectangle in world pixels.
        width: Width of the rectangle in pixels.
        height: Height of the rectangle in pixels.
    """

    x: float
    y: float
    width: float
    height: float


class CollisionEllipse(NamedTuple):
    """Ellipse expressed as its axis-aligned bounding box in world pixels.

    Attributes:
        x: Left edge of the bounding box in world pixels.
        y: Top edge of the bounding box in world pixels.
        width: Width of the bounding box in pixels.
        height: Height of the bounding box in pixels.
    """

    x: float
    y: float
    width: float
    height: float


class CollisionPolygon(NamedTuple):
    """Polygon as a list of vertices in world pixel coordinates.

    Attributes:
        points: Vertices of the polygon in world pixels, in the same winding
            order as the source object after flipping is applied.
    """

    points: List[OrderedPair]


# The supported world-space collision shapes.
CollisionShape = Union[CollisionRectangle, CollisionEllipse, CollisionPolygon]


@attr.s(auto_attribs=True)
class TileCollision:
    """A single collision shape from a tile, reprojected into world coordinates.

    Attributes:
        layer: The [TileLayer][pytiled_parser.layer.TileLayer] the tile is on.
        tile_coordinates: Location of the tile within the map, in tiles. For
            infinite maps this is the absolute tile coordinate and may be negative.
        gid: The raw global tile ID from the layer data, including flip flags.
        tile_id: The local ID of the tile within its tileset.
        tileset: The [Tileset][pytiled_parser.tileset.Tileset] the tile came from.
        shape: The world-space collision geometry.
    """

    layer: TileLayer
    tile_coordinates: OrderedPair
    gid: int
    tile_id: int
    tileset: Tileset
    shape: CollisionShape


def _iter_tile_layers(
    layers: List[Layer], offset: OrderedPair
) -> Iterator[Tuple[TileLayer, OrderedPair]]:
    """Yield each TileLayer in `layers` with its fully accumulated pixel offset.

    LayerGroup offsets are added to the offset of every layer nested inside them,
    recursively.
    """
    for layer in layers:
        accumulated = OrderedPair(offset.x + layer.offset.x, offset.y + layer.offset.y)
        if isinstance(layer, TileLayer):
            yield layer, accumulated
        elif isinstance(layer, LayerGroup):
            yield from _iter_tile_layers(layer.layers or [], accumulated)


def _iter_gids(tile_layer: TileLayer) -> Iterator[Tuple[OrderedPair, int]]:
    """Yield (tile coordinates, raw GID) for every cell of a TileLayer.

    Handles both finite layers (`data`) and infinite layers (`chunks`). Chunk
    coordinates are absolute tile coordinates, so they may be negative.
    """
    if tile_layer.data is not None:
        for row, line in enumerate(tile_layer.data):
            for column, gid in enumerate(line):
                yield OrderedPair(
                    tile_layer.coordinates.x + column,
                    tile_layer.coordinates.y + row,
                ), gid
    elif tile_layer.chunks is not None:
        for chunk in tile_layer.chunks:
            for row, line in enumerate(chunk.data):
                for column, gid in enumerate(line):
                    yield OrderedPair(
                        chunk.coordinates.x + column,
                        chunk.coordinates.y + row,
                    ), gid


def _get_tileset(tiled_map: TiledMap, gid: int) -> Tuple[Tileset, int]:
    """Resolve a flag-stripped GID to its (tileset, local tile id).

    Raises:
        InvalidTileGidError: If no tileset contains the GID.
    """
    for firstgid in sorted(tiled_map.tilesets, reverse=True):
        tileset = tiled_map.tilesets[firstgid]
        if gid >= firstgid:
            tile_id = gid - firstgid
            if tile_id >= tileset.tile_count:
                break
            return tileset, tile_id

    raise InvalidTileGidError(
        f"GID {gid} does not belong to any tileset in map "
        f"'{tiled_map.map_file}' (firstgids: {sorted(tiled_map.tilesets)})"
    )


def _flip_point(
    x: float,
    y: float,
    width: float,
    height: float,
    hflip: bool,
    vflip: bool,
    dflip: bool,
) -> OrderedPair:
    """Apply Tiled's GID flip flags to a point inside a `width` x `height` frame.

    The diagonal flip swaps the axes first, then horizontal and vertical flips
    are applied, matching Tiled's rendering of flipped tiles.
    """
    if dflip:
        x, y = y, x
        width, height = height, width
    if hflip:
        x = width - x
    if vflip:
        y = height - y
    return OrderedPair(x, y)


def _object_to_shape(
    tiled_object: tiled_object_.TiledObject,
    frame_width: float,
    frame_height: float,
    hflip: bool,
    vflip: bool,
    dflip: bool,
) -> Optional[CollisionShape]:
    """Convert a tile collision object into a flipped, tile-local collision shape.

    Returns None for unsupported object types (point, polyline, text, tile).
    """
    if tiled_object.rotation != 0:
        raise UnsupportedCollisionObjectError(
            f"Rotated collision objects are not supported, object "
            f"'{tiled_object.name}' (id {tiled_object.id}) has rotation "
            f"{tiled_object.rotation}"
        )

    x = tiled_object.coordinates.x
    y = tiled_object.coordinates.y
    width = tiled_object.size.width
    height = tiled_object.size.height

    if isinstance(tiled_object, tiled_object_.Rectangle):
        corners = [
            _flip_point(cx, cy, frame_width, frame_height, hflip, vflip, dflip)
            for cx, cy in (
                (x, y),
                (x + width, y),
                (x + width, y + height),
                (x, y + height),
            )
        ]
        xs = [corner.x for corner in corners]
        ys = [corner.y for corner in corners]
        return CollisionRectangle(
            min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)
        )

    if isinstance(tiled_object, tiled_object_.Ellipse):
        center = _flip_point(
            x + width / 2,
            y + height / 2,
            frame_width,
            frame_height,
            hflip,
            vflip,
            dflip,
        )
        if dflip:
            width, height = height, width
        return CollisionEllipse(
            center.x - width / 2, center.y - height / 2, width, height
        )

    if isinstance(tiled_object, tiled_object_.Polygon):
        points = [
            _flip_point(
                x + point.x, y + point.y, frame_width, frame_height, hflip, vflip, dflip
            )
            for point in tiled_object.points
        ]
        return CollisionPolygon(points)

    return None


def _translate(shape: CollisionShape, offset: OrderedPair) -> CollisionShape:
    """Move a collision shape by a pixel offset."""
    if isinstance(shape, CollisionPolygon):
        return CollisionPolygon(
            [
                OrderedPair(point.x + offset.x, point.y + offset.y)
                for point in shape.points
            ]
        )
    return type(shape)(
        shape.x + offset.x, shape.y + offset.y, shape.width, shape.height
    )


def extract_tile_collisions(tiled_map: TiledMap) -> List[TileCollision]:
    """Extract world-space collision shapes from every tile layer of a map.

    Walks all [TileLayers][pytiled_parser.layer.TileLayer] of the map (including
    layers nested inside [LayerGroups][pytiled_parser.layer.LayerGroup] and the
    chunks of infinite maps), resolves each GID to its tileset tile, and converts
    the tile's collision objects into world pixel coordinates. Layer and group
    offsets are accumulated onto the output, and GID flip flags (horizontal,
    vertical, diagonal) are applied to the geometry.

    Behavior for edge cases:

    - Empty cells (GID 0) are skipped.
    - Tiles whose tileset entry has no collision objects are skipped.
    - Point, polyline, text, and tile objects inside a tile's object group are
      skipped; rectangles, ellipses, and polygons are extracted.
    - Collision objects with a non-zero rotation are not supported and raise
      [UnsupportedCollisionObjectError][pytiled_parser.exception.UnsupportedCollisionObjectError].

    Args:
        tiled_map: The [TiledMap][pytiled_parser.tiled_map.TiledMap] to extract
            collision shapes from. Must have an orthogonal orientation.

    Returns:
        A list of [TileCollision][pytiled_parser.tile_collision.TileCollision]
        objects in layer/cell iteration order.

    Raises:
        UnsupportedOrientationError: If the map is not orthogonal.
        InvalidTileGidError: If a GID uses the hexagonal rotation flag or does
            not belong to any tileset in the map.
        UnsupportedCollisionObjectError: If a collision object is rotated.
    """
    if tiled_map.orientation != "orthogonal":
        raise UnsupportedOrientationError(
            f"Tile collision extraction only supports orthogonal maps, "
            f"map '{tiled_map.map_file}' has orientation "
            f"'{tiled_map.orientation}'"
        )

    collisions: List[TileCollision] = []

    tile_layers = _iter_tile_layers(tiled_map.layers, OrderedPair(0, 0))
    for tile_layer, layer_offset in tile_layers:
        for tile_coordinates, gid in _iter_gids(tile_layer):
            if gid & ROTATED_HEXAGONAL_120_FLAG:
                raise InvalidTileGidError(
                    f"GID {gid} at {tile_coordinates} on layer "
                    f"'{tile_layer.name}' uses the hexagonal 120-degree "
                    f"rotation flag, which is invalid for orthogonal maps"
                )

            hflip = bool(gid & FLIPPED_HORIZONTALLY_FLAG)
            vflip = bool(gid & FLIPPED_VERTICALLY_FLAG)
            dflip = bool(gid & FLIPPED_DIAGONALLY_FLAG)
            clean_gid = gid & GID_MASK

            if clean_gid == 0:
                continue

            tileset, tile_id = _get_tileset(tiled_map, clean_gid)

            tile: Optional[Tile] = None
            if tileset.tiles is not None:
                tile = tileset.tiles.get(tile_id)
            if tile is None or tile.objects is None:
                continue

            objects = getattr(tile.objects, "tiled_objects", None)
            if not objects:
                continue

            # Collision objects are stored in the tile's own pixel space. Image
            # collection tiles use their image size; spritesheet tiles use the
            # tileset's tile size.
            if tile.image_width and tile.image_height:
                frame_width, frame_height = tile.image_width, tile.image_height
            else:
                frame_width, frame_height = tileset.tile_width, tileset.tile_height

            tile_origin = OrderedPair(
                tile_coordinates.x * tiled_map.tile_size.width + layer_offset.x,
                tile_coordinates.y * tiled_map.tile_size.height + layer_offset.y,
            )

            for tiled_object in objects:
                shape = _object_to_shape(
                    tiled_object, frame_width, frame_height, hflip, vflip, dflip
                )
                if shape is None:
                    continue
                collisions.append(
                    TileCollision(
                        layer=tile_layer,
                        tile_coordinates=tile_coordinates,
                        gid=gid,
                        tile_id=tile_id,
                        tileset=tileset,
                        shape=_translate(shape, tile_origin),
                    )
                )

    return collisions
