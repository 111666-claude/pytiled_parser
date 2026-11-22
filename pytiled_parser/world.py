"""This module provides an implementation for World files from Tiled.

See [Tiled's docs for Worlds](https://doc.mapeditor.org/en/stable/manual/worlds/)
for more info about worlds and how to use them.

The functionality within PyTiled Parser is to load the world and outline the size
and position of each map, and provide the path to the map file. Loading a world
does not automatically load each map within the world, this is so that the game
or engine implementation can decide how to handle map loading.
"""

import re
from os import listdir
from os.path import isfile, join
from pathlib import Path
from typing import Any, List, Tuple

import attr
from typing_extensions import TypedDict

from pytiled_parser import diagnostics
from pytiled_parser.common_types import OrderedPair, Size
from pytiled_parser.diagnostics import JsonDocument
from pytiled_parser.exception import (
    FieldTypeError,
    InvalidFieldValueError,
    UnrecognizedFormat,
)


@attr.s(auto_attribs=True)
class WorldMap:
    """Represents a map within a world.

    This object can be accessed to load in a map after loading the world.

    This class translates to each object within the `maps` list of a
    World file

    Attributes:
        map_file: A Path object to the map file, can be passed to
            the pytiled_parser.parse_map function later.
        size: The size of the map in pixels
        coordinates: The position of the map within the world in pixels
    """

    map_file: Path
    size: Size
    coordinates: OrderedPair


@attr.s(auto_attribs=True)
class World:
    """Represents a world file.

    Attributes:
        maps: The list of maps within the world. These are not fully parsed
            TiledMap objects, but rather WorldMap objects which can be used
            to later parse each individual map.
        only_show_adjacent: Largely only used by the Tiled editor to determine
            if only maps adjacent to the active one should be displayed. This
            could be used to determine implementation behavior as well though
            to toggle auto-loading of adjacent maps or something of the sort.
    """

    maps: List[WorldMap]
    only_show_adjacent: bool = False


class RawPattern(TypedDict):
    regexp: str
    multiplierX: float
    multiplierY: float
    offsetX: float
    offsetY: float


class RawWorldMap(TypedDict):
    fileName: str
    height: float
    width: float
    x: float
    y: float


class RawWorld(TypedDict):
    maps: List[RawWorldMap]
    patterns: List[RawPattern]
    onlyShowAdjacentMaps: bool


def _parse_world_map(raw_world_map: RawWorldMap, map_file: Path) -> WorldMap:
    """Parse the RawWorldMap into a WorldMap.

    Args:
        raw_world_map: The RawWorldMap to parse
        map_file: The file of tiled_map to parse

    Returns:
        WorldMap: The parsed WorldMap object
    """
    return WorldMap(
        map_file=map_file,
        size=Size(
            diagnostics.json_require(raw_world_map, "width", (int, float)),
            diagnostics.json_require(raw_world_map, "height", (int, float)),
        ),
        coordinates=OrderedPair(
            diagnostics.json_require(raw_world_map, "x", (int, float)),
            diagnostics.json_require(raw_world_map, "y", (int, float)),
        ),
    )


def parse_world(file: Path) -> World:
    """Parse the raw world into a pytiled_parser type

    Args:
        file: Path to the world's file

    Returns:
        World: A properly parsed [World][pytiled_parser.world.World]
    """

    document = diagnostics.load_document(file)
    if not isinstance(document, JsonDocument):
        raise UnrecognizedFormat(
            "Expected a JSON world file, but '{}' contains XML".format(
                document.display_file
            ),
            file=document.display_file,
            reason="file content does not match its detected format",
        )

    raw_world = document.data
    if not isinstance(raw_world, dict):
        raise diagnostics.json_error(
            FieldTypeError,
            "Expected the world file to contain a JSON object, got {}".format(
                type(raw_world).__name__
            ),
            reason="world files contain a JSON object",
        )

    parent_dir = file.parent

    maps: List[WorldMap] = []

    with diagnostics.enter(document=document, parent_dir=parent_dir):
        raw_maps = raw_world.get("maps")
        if raw_maps:
            if not isinstance(raw_maps, list):
                raise diagnostics.json_error(
                    FieldTypeError,
                    "Expected an array for 'maps', got {}".format(
                        type(raw_maps).__name__
                    ),
                    path=("maps",),
                    reason="maps must be an array",
                )
            for index, raw_map in enumerate(raw_maps):
                map_path = ("maps", index)
                if not isinstance(raw_map, dict):
                    raise diagnostics.json_error(
                        FieldTypeError,
                        "Expected an object for map at index {}, got {}".format(
                            index, type(raw_map).__name__
                        ),
                        path=map_path,
                        reason="world maps are JSON objects",
                    )
                with diagnostics.enter(json_path=map_path):
                    file_name = diagnostics.json_require(raw_map, "fileName", str)
                    parsed_map_file = Path(parent_dir / file_name)
                    maps.append(
                        _parse_world_map(
                            raw_map, parsed_map_file  # type: ignore[arg-type]
                        )
                    )

        raw_patterns = raw_world.get("patterns")
        if raw_patterns:
            if not isinstance(raw_patterns, list):
                raise diagnostics.json_error(
                    FieldTypeError,
                    "Expected an array for 'patterns', got {}".format(
                        type(raw_patterns).__name__
                    ),
                    path=("patterns",),
                    reason="patterns must be an array",
                )
            for pattern_index, raw_pattern in enumerate(raw_patterns):
                pattern_path = ("patterns", pattern_index)
                if not isinstance(raw_pattern, dict):
                    raise diagnostics.json_error(
                        FieldTypeError,
                        "Expected an object for pattern at index {}, got {}".format(
                            pattern_index, type(raw_pattern).__name__
                        ),
                        path=pattern_path,
                        reason="patterns are JSON objects",
                    )
                with diagnostics.enter(json_path=pattern_path):
                    maps.extend(
                        _parse_pattern(
                            raw_pattern,  # type: ignore[arg-type]
                            parent_dir,
                            pattern_path,
                        )
                    )

        world = World(maps=maps)

        if raw_world.get("onlyShowAdjacentMaps") is not None:
            world.only_show_adjacent = bool(
                diagnostics.json_require(raw_world, "onlyShowAdjacentMaps", bool)
            )

    return world


def _parse_pattern(
    raw_pattern: RawPattern,
    parent_dir: Path,
    pattern_path: Tuple[Any, ...],
) -> List[WorldMap]:
    regex_source = diagnostics.json_require(raw_pattern, "regexp", str)
    try:
        regex = re.compile(regex_source)
    except re.error as error:
        raise diagnostics.json_error(
            FieldTypeError,
            "Invalid regular expression {!r}: {}".format(regex_source, error),
            path=("regexp",),
            reason=str(error),
        ) from error

    width = diagnostics.json_require(raw_pattern, "multiplierX", (int, float))
    height = diagnostics.json_require(raw_pattern, "multiplierY", (int, float))

    offset_x = 0.0
    offset_y = 0.0

    if raw_pattern.get("offsetX"):
        offset_x = float(diagnostics.json_require(raw_pattern, "offsetX", (int, float)))

    if raw_pattern.get("offsetY"):
        offset_y = float(diagnostics.json_require(raw_pattern, "offsetY", (int, float)))

    parsed_maps: List[WorldMap] = []

    map_files = [
        f for f in listdir(parent_dir) if isfile(join(parent_dir, f)) and regex.match(f)
    ]
    for map_file in map_files:
        search = regex.search(map_file)
        if not search:
            continue
        try:
            x = (float(search.group(1)) * width) + offset_x
            y = (float(search.group(2)) * height) + offset_y
        except (IndexError, TypeError, ValueError) as error:
            raise diagnostics.json_error(
                InvalidFieldValueError,
                "Pattern regexp {!r} must capture two numeric groups; matched "
                "{!r}".format(regex_source, map_file),
                path=("regexp",),
                reason=str(error),
            ) from error

        raw_world_map: RawWorldMap = {
            "fileName": map_file,
            "width": width,
            "height": height,
            "x": x,
            "y": y,
        }

        map_path = Path(parent_dir / map_file)
        parsed_maps.append(_parse_world_map(raw_world_map, map_path))

    return parsed_maps
