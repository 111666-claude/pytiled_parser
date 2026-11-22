from pathlib import Path
from typing import Any, List, Optional, Union, cast

from typing_extensions import TypedDict

from pytiled_parser import diagnostics
from pytiled_parser.common_types import OrderedPair
from pytiled_parser.diagnostics import JsonPath
from pytiled_parser.exception import (
    FieldTypeError,
    InvalidFieldValueError,
)
from pytiled_parser.parsers.json.layer import RawLayer
from pytiled_parser.parsers.json.layer import parse as parse_layer
from pytiled_parser.parsers.json.properties import RawProperty
from pytiled_parser.parsers.json.properties import parse as parse_properties
from pytiled_parser.parsers.json.wang_set import RawWangSet
from pytiled_parser.parsers.json.wang_set import parse as parse_wangset
from pytiled_parser.tileset import Frame, Grid, Tile, Tileset, Transformations
from pytiled_parser.util import parse_color

RawFrame = TypedDict("RawFrame", {"duration": int, "tileid": int})
RawFrame.__doc__ = """
    The keys and their types that appear in a Frame JSON Object.
"""


RawTileOffset = TypedDict("RawTileOffset", {"x": int, "y": int})
RawTileOffset.__doc__ = """
    The keys and their types that appear in a TileOffset JSON Object.
"""


RawTransformations = TypedDict(
    "RawTransformations",
    {"hflip": bool, "vflip": bool, "rotate": bool, "preferuntransformed": bool},
)
RawTransformations.__doc__ = """
    The keys and their types that appear in a Transformations JSON Object.
"""


RawTile = TypedDict(
    "RawTile",
    {
        "animation": List[RawFrame],
        "class": str,
        "id": int,
        "image": str,
        "imageheight": int,
        "imagewidth": int,
        "opacity": float,
        "type": str,
        "properties": List[RawProperty],
        "objectgroup": RawLayer,
        "x": int,
        "y": int,
        "width": int,
        "height": int,
    },
)
RawTile.__doc__ = """
    The keys and their types that appear in a Tile JSON Object.
"""


RawGrid = TypedDict("RawGrid", {"height": int, "width": int, "orientation": str})
RawGrid.__doc__ = """
    The keys and their types that appear in a Grid JSON Object.
"""


RawTileSet = TypedDict(
    "RawTileSet",
    {
        "backgroundcolor": str,
        "class": str,
        "columns": int,
        "firstgid": int,
        "grid": RawGrid,
        "image": str,
        "imageheight": int,
        "imagewidth": int,
        "margin": int,
        "name": str,
        "properties": List[RawProperty],
        "fillmode": str,
        "objectalignment": str,
        "source": str,
        "spacing": int,
        "tilecount": int,
        "tiledversion": str,
        "tileheight": int,
        "tileoffset": RawTileOffset,
        "tilerendersize": str,
        "tiles": List[RawTile],
        "tilewidth": int,
        "transparentcolor": str,
        "transformations": RawTransformations,
        "version": Union[str, float],
        "wangsets": List[RawWangSet],
    },
)
RawTileSet.__doc__ = """
    The keys and their types that appear in a TileSet JSON Object.
"""


def _parse_frame(raw_frame: RawFrame, *, path: JsonPath = ()) -> Frame:
    tileid = diagnostics.json_require(raw_frame, "tileid", int, path=path)
    duration = diagnostics.json_require(raw_frame, "duration", int, path=path)
    return Frame(duration=duration, tile_id=tileid)  # type: ignore[arg-type]


def _parse_tile_offset(raw_tile_offset: RawTileOffset) -> OrderedPair:
    x = diagnostics.json_require(raw_tile_offset, "x", int)
    y = diagnostics.json_require(raw_tile_offset, "y", int)
    return OrderedPair(x, y)  # type: ignore[arg-type]


def _parse_transformations(
    raw_transformations: RawTransformations,
) -> Transformations:
    return Transformations(
        hflip=diagnostics.json_require(raw_transformations, "hflip", bool),
        vflip=diagnostics.json_require(raw_transformations, "vflip", bool),
        rotate=diagnostics.json_require(raw_transformations, "rotate", bool),
        prefer_untransformed=diagnostics.json_require(
            raw_transformations, "preferuntransformed", bool
        ),
    )


def _parse_grid(raw_grid: RawGrid) -> Grid:
    return Grid(
        orientation=diagnostics.json_require(raw_grid, "orientation", str),
        width=diagnostics.json_require(raw_grid, "width", int),
        height=diagnostics.json_require(raw_grid, "height", int),
    )


def _parse_tile(
    raw_tile: RawTile,
    external_path: Optional[Path] = None,
    *,
    path: JsonPath = (),
) -> Tile:
    id_ = diagnostics.json_require(raw_tile, "id", int, path=path)
    tile = Tile(id=id_)  # type: ignore[arg-type]

    if raw_tile.get("animation") is not None:
        raw_animation = diagnostics.json_require(raw_tile, "animation", list, path=path)
        tile.animation = []
        for index, frame in enumerate(cast(List[RawFrame], raw_animation)):
            if not isinstance(frame, dict):
                raise diagnostics.json_error(
                    FieldTypeError,
                    "Expected an object for animation frame {}, got {}".format(
                        index, type(frame).__name__
                    ),
                    path=path + ("animation", index),
                    reason="animation frames are objects",
                )
            tile.animation.append(_parse_frame(frame, path=path + ("animation", index)))

    if raw_tile.get("objectgroup") is not None:
        objectgroup = diagnostics.json_require(raw_tile, "objectgroup", dict, path=path)
        tile.objects = parse_layer(
            objectgroup, path=path + ("objectgroup",)  # type: ignore[arg-type]
        )

    if raw_tile.get("properties") is not None:
        properties = diagnostics.json_require(raw_tile, "properties", list, path=path)
        tile.properties = parse_properties(
            properties, path=path + ("properties",)  # type: ignore[arg-type]
        )

    if raw_tile.get("image") is not None:
        image = cast(str, diagnostics.json_require(raw_tile, "image", str, path=path))
        if external_path:
            tile.image = Path(external_path / image).absolute().resolve()
        else:
            tile.image = Path(image)

    # These are ignored from coverage because there does not exist a scenario where
    # image is set, but these aren't, so the branches will never fully be hit.
    # However, leaving these checks in place is nice to prevent fatal errors on
    # a manually edited map that has an "incorrect" but not "unusable" structure
    #
    # We also set the width and height attributes here as the values for these in
    # Tiled defaults to the same value as imagewidth and imageheight if no custom value
    # is set. We then later load in the custom value if it exists.
    if raw_tile.get("imagewidth") is not None:  # pragma: no cover
        tile.image_width = cast(
            int, diagnostics.json_require(raw_tile, "imagewidth", int, path=path)
        )
        tile.width = tile.image_width

    if raw_tile.get("imageheight") is not None:  # pragma: no cover
        tile.image_height = cast(
            int, diagnostics.json_require(raw_tile, "imageheight", int, path=path)
        )
        tile.height = tile.image_height

    if raw_tile.get("type") is not None:
        tile.class_ = cast(
            str, diagnostics.json_require(raw_tile, "type", str, path=path)
        )

    if raw_tile.get("class") is not None:
        tile.class_ = cast(
            str, diagnostics.json_require(raw_tile, "class", str, path=path)
        )

    if raw_tile.get("x") is not None:
        tile.x = cast(int, diagnostics.json_require(raw_tile, "x", int, path=path))

    if raw_tile.get("y") is not None:
        tile.y = cast(int, diagnostics.json_require(raw_tile, "y", int, path=path))

    if raw_tile.get("width") is not None:
        tile.width = cast(
            int, diagnostics.json_require(raw_tile, "width", int, path=path)
        )

    if raw_tile.get("height") is not None:
        tile.height = cast(
            int, diagnostics.json_require(raw_tile, "height", int, path=path)
        )

    return tile


def parse(
    raw_tileset: RawTileSet,
    firstgid: int,
    external_path: Optional[Path] = None,
    *,
    path: JsonPath = (),
) -> Tileset:
    """Parse the raw tileset into a pytiled_parser type

    Args:
        raw_tileset: Raw Tileset to be parsed.
        firstgid: GID corresponding the first tile in the set.
        external_path: The path to the tileset if it is not an embedded one.
        path: Path to this tileset within the JSON document.

    Returns:
        TileSet: a properly typed TileSet.
    """
    if not isinstance(raw_tileset, dict):
        raise diagnostics.json_error(
            FieldTypeError,
            "Expected a tileset object, got {}".format(type(raw_tileset).__name__),
            path=path,
            reason="tilesets are JSON objects",
        )

    frame = diagnostics.current_frame()
    with diagnostics.enter(
        json_path=path,
        field_prefix="",
        layer=frame.layer,
        object=frame.object,
    ):
        return _parse_tileset(raw_tileset, firstgid, external_path, path=())


def _parse_tileset(
    raw_tileset: RawTileSet,
    firstgid: int,
    external_path: Optional[Path],
    *,
    path: JsonPath,
) -> Tileset:
    name = cast(str, diagnostics.json_require(raw_tileset, "name", str, path=path))
    tile_count = cast(
        int, diagnostics.json_require(raw_tileset, "tilecount", int, path=path)
    )
    tile_width = cast(
        int, diagnostics.json_require(raw_tileset, "tilewidth", int, path=path)
    )
    tile_height = cast(
        int, diagnostics.json_require(raw_tileset, "tileheight", int, path=path)
    )
    columns = cast(
        int,
        diagnostics.json_optional(raw_tileset, "columns", int, path=path, default=1),
    )
    spacing = cast(
        int, diagnostics.json_require(raw_tileset, "spacing", int, path=path)
    )
    margin = cast(int, diagnostics.json_require(raw_tileset, "margin", int, path=path))

    tileset = Tileset(
        name=name,
        tile_count=tile_count,
        tile_width=tile_width,
        tile_height=tile_height,
        columns=columns,
        spacing=spacing,
        margin=margin,
        firstgid=firstgid,
    )

    if raw_tileset.get("version") is not None:
        version = raw_tileset["version"]
        if isinstance(version, bool) or not isinstance(version, (str, float, int)):
            raise diagnostics.json_error(
                FieldTypeError,
                "Expected a string or number for 'version', got {}".format(
                    type(version).__name__
                ),
                path=path + ("version",),
                reason="tileset versions are strings or numbers",
            )
        # This is here to support old versions of Tiled Maps. It's a pain
        # to keep old versions in the test data and not update them with the
        # rest so I'm excluding this from coverage. In reality it's probably
        # not needed. Tiled hasn't been using floats for the version for a long time
        if isinstance(version, float):  # pragma: no cover
            tileset.version = str(version)
        else:
            tileset.version = version  # type: ignore[assignment]

    if raw_tileset.get("tiledversion") is not None:
        tileset.tiled_version = cast(
            str,
            diagnostics.json_optional(raw_tileset, "tiledversion", str, path=path),
        )

    if raw_tileset.get("image") is not None:
        image = cast(
            str, diagnostics.json_optional(raw_tileset, "image", str, path=path)
        )
        if external_path:
            tileset.image = Path(external_path / image).absolute().resolve()
        else:
            tileset.image = Path(image)

    if raw_tileset.get("imagewidth") is not None:  # pragma: no cover
        tileset.image_width = cast(
            int,
            diagnostics.json_optional(raw_tileset, "imagewidth", int, path=path),
        )

    if raw_tileset.get("imageheight") is not None:  # pragma: no cover
        tileset.image_height = cast(
            int,
            diagnostics.json_optional(raw_tileset, "imageheight", int, path=path),
        )

    if raw_tileset.get("objectalignment") is not None:
        tileset.alignment = cast(
            str,
            diagnostics.json_optional(raw_tileset, "objectalignment", str, path=path),
        )

    if raw_tileset.get("backgroundcolor") is not None:
        background = cast(
            str,
            diagnostics.json_optional(raw_tileset, "backgroundcolor", str, path=path),
        )
        try:
            tileset.background_color = parse_color(background)
        except InvalidFieldValueError as error:
            raise diagnostics.json_error(
                InvalidFieldValueError,
                "Invalid background color {!r}".format(background),
                path=path + ("backgroundcolor",),
                reason=error.reason or str(error),
            ) from error

    if raw_tileset.get("tileoffset") is not None:
        tile_offset = diagnostics.json_require(
            raw_tileset, "tileoffset", dict, path=path
        )
        tileset.tile_offset = _parse_tile_offset(tile_offset)

    if raw_tileset.get("transparentcolor") is not None:
        transparent = cast(
            str,
            diagnostics.json_optional(raw_tileset, "transparentcolor", str, path=path),
        )
        try:
            tileset.transparent_color = parse_color(transparent)
        except InvalidFieldValueError as error:
            raise diagnostics.json_error(
                InvalidFieldValueError,
                "Invalid transparent color {!r}".format(transparent),
                path=path + ("transparentcolor",),
                reason=error.reason or str(error),
            ) from error

    if raw_tileset.get("grid") is not None:
        grid = diagnostics.json_require(raw_tileset, "grid", dict, path=path)
        tileset.grid = _parse_grid(grid)

    if raw_tileset.get("properties") is not None:
        properties = diagnostics.json_require(
            raw_tileset, "properties", list, path=path
        )
        tileset.properties = parse_properties(
            properties, path=path + ("properties",)  # type: ignore[arg-type]
        )

    if raw_tileset.get("tiles") is not None:
        tiles = {}
        raw_tiles = raw_tileset["tiles"]
        if isinstance(raw_tiles, dict):
            for raw_tile_id, raw_tile in raw_tiles.items():
                tile_path = path + ("tiles", str(raw_tile_id))
                if not isinstance(raw_tile, dict):
                    raise diagnostics.json_error(
                        FieldTypeError,
                        "Expected an object for tile {!r}, got {}".format(
                            raw_tile_id, type(raw_tile).__name__
                        ),
                        path=tile_path,
                        reason="tiles are JSON objects",
                    )
                if raw_tile.get("id") is not None:
                    raise diagnostics.json_error(
                        InvalidFieldValueError,
                        "Tile {!r} must not declare an 'id' when tiles is a "
                        "mapping".format(raw_tile_id),
                        path=tile_path + ("id",),
                        reason="the tile id comes from the mapping key",
                    )
                try:
                    tile_id = int(raw_tile_id)
                except (TypeError, ValueError) as error:
                    raise diagnostics.json_error(
                        FieldTypeError,
                        "Tile mapping key {!r} is not an integer".format(raw_tile_id),
                        path=tile_path,
                        reason=str(error),
                    ) from error
                raw_tile["id"] = tile_id
                tiles[tile_id] = _parse_tile(
                    raw_tile, external_path=external_path, path=tile_path
                )
        elif isinstance(raw_tiles, list):
            for index, raw_tile in enumerate(raw_tiles):
                tile_path = path + ("tiles", index)
                if not isinstance(raw_tile, dict):
                    raise diagnostics.json_error(
                        FieldTypeError,
                        "Expected an object for tile at index {}, got {}".format(
                            index, type(raw_tile).__name__
                        ),
                        path=tile_path,
                        reason="tiles are JSON objects",
                    )
                tile_id = cast(
                    int,
                    diagnostics.json_require(raw_tile, "id", int, path=tile_path),
                )
                tiles[tile_id] = _parse_tile(
                    raw_tile, external_path=external_path, path=tile_path
                )
        else:
            raise diagnostics.json_error(
                FieldTypeError,
                "Expected an array or object for 'tiles', got {}".format(
                    type(raw_tiles).__name__
                ),
                path=path + ("tiles",),
                reason="tiles must be an array or a mapping",
            )
        tileset.tiles = tiles

    if raw_tileset.get("wangsets") is not None:
        raw_wangsets = diagnostics.json_require(
            raw_tileset, "wangsets", list, path=path
        )
        wangsets = []
        for index, raw_wangset in enumerate(cast(List[Any], raw_wangsets)):
            wangset_path = path + ("wangsets", index)
            if not isinstance(raw_wangset, dict):
                raise diagnostics.json_error(
                    FieldTypeError,
                    "Expected an object for wangset at index {}, got {}".format(
                        index, type(raw_wangset).__name__
                    ),
                    path=wangset_path,
                    reason="wangsets are JSON objects",
                )
            wangsets.append(
                parse_wangset(raw_wangset, path=wangset_path)  # type: ignore[arg-type]
            )
        tileset.wang_sets = wangsets

    if raw_tileset.get("transformations") is not None:
        transformations = diagnostics.json_require(
            raw_tileset, "transformations", dict, path=path
        )
        tileset.transformations = _parse_transformations(transformations)

    if raw_tileset.get("class") is not None:
        tileset.class_ = cast(
            str, diagnostics.json_optional(raw_tileset, "class", str, path=path)
        )

    if raw_tileset.get("tilerendersize") is not None:
        tileset.tile_render_size = cast(
            str,
            diagnostics.json_optional(raw_tileset, "tilerendersize", str, path=path),
        )

    if raw_tileset.get("fillmode") is not None:
        tileset.fill_mode = cast(
            str, diagnostics.json_optional(raw_tileset, "fillmode", str, path=path)
        )

    return tileset
