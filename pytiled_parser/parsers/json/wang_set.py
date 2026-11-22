from typing import Any, List, cast

from typing_extensions import TypedDict

from pytiled_parser import diagnostics
from pytiled_parser.diagnostics import JsonPath
from pytiled_parser.exception import FieldTypeError, InvalidFieldValueError
from pytiled_parser.parsers.json.properties import RawProperty
from pytiled_parser.parsers.json.properties import parse as parse_properties
from pytiled_parser.util import parse_color
from pytiled_parser.wang_set import WangColor, WangSet, WangTile

RawWangTile = TypedDict(
    "RawWangTile",
    {
        "tileid": int,
        # Tiled stores these IDs as a list represented like so:
        # [top, top_right, right, bottom_right, bottom, bottom_left, left, top_left]
        "wangid": List[int],
    },
)
RawWangTile.__doc__ = """
    The keys and their types that appear in a Wang Tile JSON Object.
"""


RawWangColor = TypedDict(
    "RawWangColor",
    {
        "color": str,
        "class": str,
        "name": str,
        "probability": float,
        "tile": int,
        "properties": List[RawProperty],
    },
)
RawWangColor.__doc__ = """
    The keys and their types that appear in a Wang Color JSON Object.
"""


RawWangSet = TypedDict(
    "RawWangSet",
    {
        "colors": List[RawWangColor],
        "class": str,
        "name": str,
        "properties": List[RawProperty],
        "tile": int,
        "type": str,
        "wangtiles": List[RawWangTile],
    },
)
RawWangSet.__doc__ = """
    The keys and their types that appear in a Wang Set JSON Object.
"""


def _parse_wang_tile(raw_wang_tile: RawWangTile, *, path: JsonPath = ()) -> WangTile:
    tile_id = diagnostics.json_require(raw_wang_tile, "tileid", int, path=path)
    raw_wang_id = diagnostics.json_require(raw_wang_tile, "wangid", list, path=path)

    wang_id = []
    for index, value in enumerate(cast(List[Any], raw_wang_id)):
        if isinstance(value, bool) or not isinstance(value, int):
            raise diagnostics.json_error(
                FieldTypeError,
                "Expected integers in 'wangid', got {} at index {}".format(
                    type(value).__name__, index
                ),
                path=path + ("wangid", index),
                reason="wang ids are integers",
            )
        wang_id.append(value)

    return WangTile(tile_id=tile_id, wang_id=wang_id)  # type: ignore[arg-type]


def _parse_wang_color(
    raw_wang_color: RawWangColor, *, path: JsonPath = ()
) -> WangColor:
    name = diagnostics.json_require(raw_wang_color, "name", str, path=path)
    color = diagnostics.json_require(raw_wang_color, "color", str, path=path)
    tile = diagnostics.json_require(raw_wang_color, "tile", int, path=path)
    probability = diagnostics.json_require(
        raw_wang_color, "probability", (int, float), path=path
    )

    try:
        parsed_color = parse_color(color)
    except InvalidFieldValueError as error:
        raise diagnostics.json_error(
            InvalidFieldValueError,
            "Invalid wang color {!r}".format(color),
            path=path + ("color",),
            reason=error.reason or str(error),
        ) from error

    wang_color = WangColor(
        name=name,
        color=parsed_color,
        tile=tile,
        probability=float(probability),
    )

    if raw_wang_color.get("properties") is not None:
        properties = diagnostics.json_require(
            raw_wang_color, "properties", list, path=path
        )
        wang_color.properties = parse_properties(
            properties, path=path + ("properties",)  # type: ignore[arg-type]
        )

    return wang_color


def parse(raw_wangset: RawWangSet, *, path: JsonPath = ()) -> WangSet:
    """Parse the raw wangset into a pytiled_parser type

    Args:
        raw_wangset: Raw Wangset to be parsed.
        path: Path to this wangset in the JSON document.

    Returns:
        WangSet: A properly typed WangSet.
    """
    if not isinstance(raw_wangset, dict):
        raise diagnostics.json_error(
            FieldTypeError,
            "Expected a wangset object, got {}".format(type(raw_wangset).__name__),
            path=path,
            reason="wangsets are JSON objects",
        )

    with diagnostics.enter(json_path=path, field_prefix=""):
        return _parse_wangset(raw_wangset, path=())


def _parse_wangset(raw_wangset: RawWangSet, *, path: JsonPath) -> WangSet:
    name = diagnostics.json_require(raw_wangset, "name", str, path=path)
    tile = diagnostics.json_require(raw_wangset, "tile", int, path=path)
    wang_type = diagnostics.json_require(raw_wangset, "type", str, path=path)

    raw_colors = diagnostics.json_require(raw_wangset, "colors", list, path=path)
    colors = []
    for index, raw_wang_color in enumerate(cast(List[Any], raw_colors)):
        color_path = path + ("colors", index)
        if not isinstance(raw_wang_color, dict):
            raise diagnostics.json_error(
                FieldTypeError,
                "Expected an object for wang color at index {}, got {}".format(
                    index, type(raw_wang_color).__name__
                ),
                path=color_path,
                reason="wang colors are JSON objects",
            )
        colors.append(
            _parse_wang_color(raw_wang_color, path=color_path)  # type: ignore[arg-type]
        )

    raw_tiles = diagnostics.json_require(raw_wangset, "wangtiles", list, path=path)
    tiles = {}
    for index, raw_wang_tile in enumerate(cast(List[Any], raw_tiles)):
        tile_path = path + ("wangtiles", index)
        if not isinstance(raw_wang_tile, dict):
            raise diagnostics.json_error(
                FieldTypeError,
                "Expected an object for wang tile at index {}, got {}".format(
                    index, type(raw_wang_tile).__name__
                ),
                path=tile_path,
                reason="wang tiles are JSON objects",
            )
        parsed_tile = _parse_wang_tile(
            raw_wang_tile, path=tile_path  # type: ignore[arg-type]
        )
        tiles[parsed_tile.tile_id] = parsed_tile

    wangset = WangSet(
        name=name,
        tile=tile,
        wang_type=wang_type,
        wang_colors=colors,
        wang_tiles=tiles,
    )

    if raw_wangset.get("properties") is not None:
        properties = diagnostics.json_require(
            raw_wangset, "properties", list, path=path
        )
        wangset.properties = parse_properties(
            properties, path=path + ("properties",)  # type: ignore[arg-type]
        )

    return wangset
