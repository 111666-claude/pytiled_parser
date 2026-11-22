"""Viewport tile queries for parsed Tiled maps.

Large infinite maps can contain thousands of chunks spread over an enormous
tile grid. Expanding every chunk into one big grid just to render the handful
of tiles that are currently on screen is both slow and error prone, especially
once negative tile coordinates are involved. This module answers the viewer's
question directly: *which non-empty tiles intersect this rectangle of tiles?*

The public entry points are
[iter_viewport_tiles][pytiled_parser.viewport.iter_viewport_tiles] and
[get_viewport_tiles][pytiled_parser.viewport.get_viewport_tiles]. Both accept
either a single [TileLayer][pytiled_parser.layer.TileLayer] (finite or
infinite) or a whole [TiledMap][pytiled_parser.tiled_map.TiledMap] with the
same call. Only the cells inside the requested rectangle are visited; chunks
and data rows outside of it are never expanded or indexed.

Coordinates and ordering
------------------------

"Global tile coordinates" are the layer's tile coordinates after the chunk
offset has been applied, i.e. ``chunk.coordinates + (column, row)``. They may
be negative. For a finite layer the data grid is anchored at the layer's
``coordinates`` attribute (``startx``/``starty`` in the JSON format, which is
``(0, 0)`` for maps saved by Tiled). The rectangle is half-open:
``x <= tile_x < x + width`` and ``y <= tile_y < y + height``.

A rectangle with a zero or negative width/height matches nothing and is
returned as an empty result without touching any tile data.

Within a single tile layer, results are ordered row by row (ascending global
``y``, then ascending global ``x``), independent of the order in which
non-overlapping chunks happen to be stored. When a whole map is queried,
layers are visited in draw order (document order, with the children of a layer
group expanded in place), so earlier layers are drawn first. Results from each
layer are still emitted row by row.

.. note::
   Chunk storage order does matter in one case: when chunks overlap, the
   later chunk wins (see below). For chunks that do not overlap, storage
   order has no effect on the output.

Layer visibility is deliberately not filtered: every tile layer in the source
is queried, including hidden layers and hidden group ancestors. A renderer
normally wants the raw cells and applies its own visibility, opacity and
parallax rules; filtering here would silently hide data. Likewise, group layer
offsets are not applied, because the rectangle is in tile coordinates and
layer offsets are a pixel-space rendering concern.

Overlapping chunks
------------------

Tiled stores each chunk separately and never overlaps them in files it
writes, but hand edited or merged files can contain overlapping chunks. The
same rule Tiled's reader applies is used here: chunks are applied in list
order, so a later chunk wins over an earlier one for any cell they share. This
also means an empty cell (GID ``0``) in a later chunk clears a tile placed by
an earlier chunk. The order of chunks within the file is preserved exactly as
parsed by both the JSON and the TMX parsers, so the two formats produce
identical results for the same data.

An empty cell in a single chunk is simply skipped: it is never returned as a
tile and contributes nothing to the result.

Tile flipping
-------------

The raw global ID is returned unchanged, including the high flip/rotation flag
bits (horizontal, vertical, diagonal, and the hexagonal 120 degree rotation
flag). A cell is considered empty only if the GID with those flag bits masked
away is ``0``; flag bits alone do not make a cell non-empty.
"""

import operator
from collections.abc import Iterable as IterableABC
from typing import (
    Any,
    Dict,
    Iterable,
    Iterator,
    List,
    NamedTuple,
    Optional,
    Tuple,
    Union,
)

from pytiled_parser.common_types import OrderedPair
from pytiled_parser.layer import Chunk, Layer, LayerGroup, TileLayer
from pytiled_parser.tiled_map import TiledMap

# The top four bits of a raw GID hold flag data: horizontal/vertical/diagonal
# flip plus the hexagonal 120 degree rotation bit. They must be masked off to
# tell whether a cell actually refers to a tile.
#
# TMX Reference:
# https://doc.mapeditor.org/en/stable/reference/tmx-map-format/#tile-flipping
_GID_MASK = 0x0FFFFFFF

TileLayerSource = Union[
    TileLayer,
    TiledMap,
    LayerGroup,
    Iterable[Layer],
]


class TileRect(NamedTuple):
    """A rectangle in global tile coordinates.

    The rectangle is half-open: a cell at ``(x + width, y)`` or
    ``(x, y + height)`` is just outside of it. Any coordinate may be
    negative. A non-positive ``width`` or ``height`` produces an empty
    result.

    Attributes:
        x: Global tile X coordinate of the top-left corner.
        y: Global tile Y coordinate of the top-left corner.
        width: Width of the rectangle in tiles.
        height: Height of the rectangle in tiles.
    """

    x: int
    y: int
    width: int
    height: int

    @property
    def right(self) -> int:
        """Exclusive right edge of the rectangle."""
        return self.x + self.width

    @property
    def bottom(self) -> int:
        """Exclusive bottom edge of the rectangle."""
        return self.y + self.height


class ViewportTile(NamedTuple):
    """A single non-empty tile that intersects the queried rectangle.

    Attributes:
        layer: The [TileLayer][pytiled_parser.layer.TileLayer] that owns the
            tile. This is the same object that was parsed from the map.
        layer_id: The layer's ``id``. May be ``None`` for manually built
            layers; the Tiled parsers always supply an ID.
        layer_name: The layer's ``name``, copied for convenient access.
        coordinates: Global tile coordinates of the cell as an
            [OrderedPair][pytiled_parser.common_types.OrderedPair] of ints.
            Both values may be negative.
        gid: The raw global ID read from the layer, including any flip flag
            bits.
        chunk: The [Chunk][pytiled_parser.layer.Chunk] the tile came from, or
            ``None`` for a finite layer. When chunks overlap this is the chunk
            whose value won.
    """

    layer: TileLayer
    layer_id: Optional[int]
    layer_name: str
    coordinates: OrderedPair
    gid: int
    chunk: Optional[Chunk]


def _as_rect_int(value: Any, name: str) -> int:
    """Validate one component of a rectangle.

    Args:
        value: The value to validate.
        name: Component name, used in error messages.

    Returns:
        The value as an int.

    Raises:
        TypeError: If the value is a bool, a float with a fractional part, or
            not integer-like at all.
    """
    if isinstance(value, bool):
        raise TypeError(f"TileRect {name} must be an integer, got a bool")

    try:
        return operator.index(value)
    except TypeError:
        pass

    if isinstance(value, float) and value.is_integer():
        return int(value)

    raise TypeError(f"TileRect {name} must be an integer, got {type(value).__name__}")


def _normalize_rect(rect: Any) -> TileRect:
    """Convert a rectangle like value into a :class:`TileRect`.

    The primary accepted form is a :class:`TileRect`. For convenience a
    four-item ``(x, y, width, height)`` sequence or any object exposing
    ``x``, ``y``, ``width`` and ``height`` attributes is also accepted.

    Args:
        rect: The rectangle to normalize.

    Returns:
        TileRect: The validated rectangle.

    Raises:
        TypeError: If the value cannot be interpreted as a tile rectangle or
            one of its components is not an integer.
    """
    if isinstance(rect, TileRect) or all(
        hasattr(rect, attribute) for attribute in ("x", "y", "width", "height")
    ):
        values = (rect.x, rect.y, rect.width, rect.height)
    elif isinstance(rect, (tuple, list)) and len(rect) == 4:
        values = tuple(rect)
    else:
        raise TypeError(
            "rect must be a TileRect, an (x, y, width, height) sequence, "
            "or an object with x/y/width/height attributes"
        )

    return TileRect(
        x=_as_rect_int(values[0], "x"),
        y=_as_rect_int(values[1], "y"),
        width=_as_rect_int(values[2], "width"),
        height=_as_rect_int(values[3], "height"),
    )


def _layer_tile_offset(layer: TileLayer) -> Tuple[int, int]:
    """Return the tile coordinate anchoring a finite layer's data grid.

    Tiled stores the start position of a layer in ``startx``/``starty``,
    which pytiled_parser exposes as ``layer.coordinates``. Tiled omits these
    fields for ordinary finite maps, so the default is ``(0, 0)``.
    """
    if layer.coordinates is None:
        return 0, 0

    return int(layer.coordinates.x), int(layer.coordinates.y)


def _is_empty_gid(gid: Any) -> bool:
    """Return whether a raw GID refers to no tile once flags are masked off."""
    if gid is None or gid == 0:
        return True

    if isinstance(gid, int):
        return not gid & _GID_MASK

    # Defensive: a malformed cell that is not an integer is treated as empty
    # rather than crashing the whole viewport query.
    return True


def _iter_finite_layer(layer: TileLayer, rect: TileRect) -> Iterator[ViewportTile]:
    """Yield tiles from a non-infinite layer's rectangular data grid."""
    data = layer.data
    if not data:
        return

    offset_x, offset_y = _layer_tile_offset(layer)

    # Clamp the rectangle to the actual data bounds before constructing any
    # range. This is what keeps an enormous viewport from touching cells that
    # do not exist, and keeps ragged rows from raising IndexError.
    row_start = max(rect.y - offset_y, 0)
    row_stop = min(rect.bottom - offset_y, len(data))

    for row_index in range(row_start, row_stop):
        row = data[row_index]
        if not row:
            continue

        column_start = max(rect.x - offset_x, 0)
        column_stop = min(rect.right - offset_x, len(row))

        for column_index in range(column_start, column_stop):
            gid = row[column_index]
            if _is_empty_gid(gid):
                continue

            yield ViewportTile(
                layer=layer,
                layer_id=layer.id,
                layer_name=layer.name,
                coordinates=OrderedPair(column_index + offset_x, row_index + offset_y),
                gid=gid,
                chunk=None,
            )


def _iter_chunk_layer(layer: TileLayer, rect: TileRect) -> Iterator[ViewportTile]:
    """Yield tiles from an infinite layer's chunks.

    Only the cell range covered by both the rectangle and each chunk's actual
    data is visited. Chunks that do not intersect the rectangle are skipped
    without reading their rows.

    Chunks are applied in list order into a sparse result mapping. This gives
    overlapping chunks a well defined priority: the last chunk that covers a
    cell wins, even when its value is ``0`` (which clears the cell).
    """
    chunks = layer.chunks
    if not chunks:
        return

    # Keyed by (global y, global x) so that the final sort is row-major.
    resolved: Dict[Tuple[int, int], Tuple[int, Chunk]] = {}

    for chunk in chunks:
        if chunk is None or chunk.coordinates is None:
            continue

        data = chunk.data
        if not data:
            continue

        chunk_x = int(chunk.coordinates.x)
        chunk_y = int(chunk.coordinates.y)

        # Never trust the declared chunk size more than the data that is
        # actually present: truncated/ragged chunks must not raise.
        if chunk.size is not None:
            declared_width = int(chunk.size.width)
            declared_height = int(chunk.size.height)
        else:
            declared_width = 0
            declared_height = 0

        available_rows = len(data)
        if declared_height > 0:
            available_rows = min(available_rows, declared_height)

        row_start = max(rect.y - chunk_y, 0)
        row_stop = min(rect.bottom - chunk_y, available_rows)
        if row_start >= row_stop:
            continue

        for row_index in range(row_start, row_stop):
            row = data[row_index]
            if not row:
                continue

            available_columns = len(row)
            if declared_width > 0:
                available_columns = min(available_columns, declared_width)

            column_start = max(rect.x - chunk_x, 0)
            column_stop = min(rect.right - chunk_x, available_columns)

            for column_index in range(column_start, column_stop):
                global_x = chunk_x + column_index
                global_y = chunk_y + row_index
                gid = row[column_index]
                key = (global_y, global_x)

                # A later chunk overrides an earlier one. An empty cell in
                # the later chunk clears the earlier value rather than
                # leaving it in place.
                if _is_empty_gid(gid):
                    resolved.pop(key, None)
                else:
                    resolved[key] = (gid, chunk)

    for global_y, global_x in sorted(resolved):
        gid, chunk = resolved[(global_y, global_x)]

        yield ViewportTile(
            layer=layer,
            layer_id=layer.id,
            layer_name=layer.name,
            coordinates=OrderedPair(global_x, global_y),
            gid=gid,
            chunk=chunk,
        )


def _iter_layer_tiles(layer: TileLayer, rect: TileRect) -> Iterator[ViewportTile]:
    """Yield tiles for one tile layer, choosing the data representation."""
    # An infinite layer stores its content as chunks. A finite layer stores
    # it as a two dimensional grid. Some hand built layers carry both; chunks
    # take precedence because that is what Tiled renders for infinite maps.
    if layer.chunks:
        yield from _iter_chunk_layer(layer, rect)
    elif layer.data is not None:
        yield from _iter_finite_layer(layer, rect)


def _flatten_tile_layers(layers: Iterable[Layer]) -> List[TileLayer]:
    """Flatten a layer tree into tile layers in draw order.

    The children of a layer group are expanded in place, matching how Tiled
    renders groups. ``id()`` based cycle detection guards against layers that
    were wired into a loop by hand.
    """
    flattened: List[TileLayer] = []
    visited: set = set()

    def walk(candidates: Iterable[Layer]) -> None:
        for candidate in candidates:
            if id(candidate) in visited:
                continue
            visited.add(id(candidate))

            if isinstance(candidate, TileLayer):
                flattened.append(candidate)
            elif isinstance(candidate, LayerGroup) and candidate.layers:
                walk(candidate.layers)

    walk(layers)
    return flattened


def _normalize_source(source: Any) -> List[TileLayer]:
    """Resolve a query source into an ordered list of tile layers.

    Args:
        source: A tile layer, a map, a layer group, or an iterable of layers.

    Returns:
        List[TileLayer]: The tile layers to query, in draw order.

    Raises:
        TypeError: If the source is not one of the accepted forms or contains
            a non-layer object.
    """
    if isinstance(source, TileLayer):
        return [source]

    if isinstance(source, TiledMap):
        return _flatten_tile_layers(source.layers)

    if isinstance(source, LayerGroup):
        return _flatten_tile_layers([source])

    if isinstance(source, IterableABC) and not isinstance(source, (str, bytes)):
        layers = list(source)
        for layer in layers:
            if not isinstance(layer, Layer):
                raise TypeError(
                    "an iterable passed as the viewport query source must "
                    "contain pytiled_parser layers"
                )
        return _flatten_tile_layers(layers)

    raise TypeError(
        "source must be a TileLayer, TiledMap, LayerGroup, or an iterable " "of layers"
    )


def iter_viewport_tiles(
    source: TileLayerSource, rect: TileRect
) -> Iterator[ViewportTile]:
    """Iterate the non-empty tiles intersecting a rectangle of tiles.

    Finite and infinite tile layers use the same entry point. When a whole
    map (or a layer group/iterable of layers) is supplied, every tile layer is
    queried in draw order with layer groups expanded in place. Only the cells
    that actually intersect ``rect`` are inspected; chunks and rows outside
    of the rectangle are not expanded.

    Args:
        source: A [TileLayer][pytiled_parser.layer.TileLayer], a
            [TiledMap][pytiled_parser.tiled_map.TiledMap], a
            [LayerGroup][pytiled_parser.layer.LayerGroup], or an iterable of
            layers.
        rect: The rectangle to query, as a
            [TileRect][pytiled_parser.viewport.TileRect], an
            ``(x, y, width, height)`` sequence, or an object with
            ``x``/``y``/``width``/``height`` attributes. It is specified in
            global tile coordinates and may contain negative values.

    Returns:
        Iterator[ViewportTile]: Non-empty tiles ordered row by row within
        each layer (ascending ``y``, then ascending ``x``). When a map is
        queried, layers appear in draw order. A rectangle with a non-positive
        width or height yields no tiles.

    Raises:
        TypeError: If ``source`` or ``rect`` is not a supported type.

    Example::

        from pytiled_parser import get_viewport_tiles, parse_map

        tiled_map = parse_map("map.json")
        visible = get_viewport_tiles(tiled_map, (-8, -8, 32, 24))
        for tile in visible:
            draw(tile.layer_name, tile.gid, tile.coordinates)
    """
    normalized_rect = _normalize_rect(rect)
    layers = _normalize_source(source)

    if normalized_rect.width <= 0 or normalized_rect.height <= 0:
        return iter(())

    def generate() -> Iterator[ViewportTile]:
        for layer in layers:
            yield from _iter_layer_tiles(layer, normalized_rect)

    return generate()


def get_viewport_tiles(source: TileLayerSource, rect: TileRect) -> List[ViewportTile]:
    """Return the non-empty tiles intersecting a rectangle of tiles.

    This is the list returning counterpart of
    [iter_viewport_tiles][pytiled_parser.viewport.iter_viewport_tiles]; see
    that function for the coordinate system, ordering and overlapping chunk
    rules.

    Args:
        source: A tile layer, map, layer group, or iterable of layers.
        rect: The rectangle to query, in global tile coordinates.

    Returns:
        List[ViewportTile]: The matching tiles, in the same order that
        :func:`iter_viewport_tiles` yields them.

    Raises:
        TypeError: If ``source`` or ``rect`` is not a supported type.
    """
    return list(iter_viewport_tiles(source, rect))
