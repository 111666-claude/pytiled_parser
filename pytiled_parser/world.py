"""This module provides an implementation for World files from Tiled.

See [Tiled's docs for Worlds](https://doc.mapeditor.org/en/stable/manual/worlds/)
for more info about worlds and how to use them.

The functionality within PyTiled Parser is to load the world and outline the size
and position of each map, and provide the path to the map file. Loading a world
does not automatically load each map within the world, this is so that the game
or engine implementation can decide how to handle map loading.

The :class:`World` object also provides helpers for the common use case of a
scrolling camera. :meth:`World.maps_in_region` performs a rectangular query
against the maps described by the world file, while
:meth:`World.load_maps` parses the maps covered by a viewport on demand,
caching the result so each map file is only ever parsed once.
"""

import json
import re
from os import listdir
from os.path import isfile, join
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Tuple

import attr
from typing_extensions import TypedDict

from pytiled_parser.common_types import OrderedPair, Size

if TYPE_CHECKING:
    from pytiled_parser.tiled_map import TiledMap


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

    def __attrs_post_init__(self) -> None:
        if self.size.width <= 0 or self.size.height <= 0:
            raise ValueError(
                f"WorldMap '{self.map_file}' has an invalid size "
                f"{self.size!r}: both width and height must be greater "
                f"than zero."
            )

    @property
    def bounds(self) -> Tuple[float, float, float, float]:
        """The map's axis-aligned bounding box in world coordinates.

        Returns:
            A tuple of ``(left, top, right, bottom)`` pixel coordinates,
            where ``right`` and ``bottom`` are exclusive edges.
        """
        return (
            self.coordinates.x,
            self.coordinates.y,
            self.coordinates.x + self.size.width,
            self.coordinates.y + self.size.height,
        )


@attr.s(auto_attribs=True)
class World:
    """Represents a world file.

    Attributes:
        maps: The list of maps within the world. These are not fully parsed
            TiledMap objects, but rather WorldMap objects which can be used
            to later parse each individual map. The list order matches the
            order of the world file: explicit ``maps`` entries first,
            followed by any maps discovered through ``patterns``.
        only_show_adjacent: Largely only used by the Tiled editor to determine
            if only maps adjacent to the active one should be displayed. This
            could be used to determine implementation behavior as well though
            to toggle auto-loading of adjacent maps or something of the sort.
    """

    maps: List[WorldMap]
    only_show_adjacent: bool = False
    # Cache of maps that have already been parsed, keyed by their resolved
    # map file path so the same file is never parsed more than once.
    _parsed_maps: Dict[Path, "TiledMap"] = attr.ib(
        factory=dict, init=False, repr=False, eq=False
    )

    def maps_in_region(
        self,
        x: float,
        y: float,
        width: float,
        height: float,
    ) -> List[WorldMap]:
        """Return the world maps that intersect a rectangular region.

        This is intended for viewport/camera queries: given the top-left
        position and size of the visible area in world pixel coordinates,
        it returns every :class:`WorldMap` whose area overlaps it. Negative
        coordinates and floating point positions are fully supported.

        Intersection is strict: two rectangles that only share an edge or a
        corner (for example a map ending at ``x == 160`` and a region
        starting at ``x == 160``) do not intersect, mirroring the
        half-open interval convention ``[left, right)``.

        The returned maps preserve the order in which they appear in
        :attr:`maps` (and therefore the world file), even when multiple
        maps overlap the region or each other.

        Args:
            x: The x coordinate of the region's top-left corner in pixels.
            y: The y coordinate of the region's top-left corner in pixels.
            width: The width of the region in pixels. Must be greater than
                zero.
            height: The height of the region in pixels. Must be greater
                than zero.

        Returns:
            A list of the :class:`WorldMap` objects intersecting the region,
            in world file order. An empty list means nothing was hit.

        Raises:
            ValueError: If ``width`` or ``height`` is zero or negative.
        """
        if width <= 0:
            raise ValueError(
                f"Region width must be greater than zero, got {width!r} "
                f"(region: x={x!r}, y={y!r}, width={width!r}, "
                f"height={height!r})."
            )
        if height <= 0:
            raise ValueError(
                f"Region height must be greater than zero, got {height!r} "
                f"(region: x={x!r}, y={y!r}, width={width!r}, "
                f"height={height!r})."
            )

        region_right = x + width
        region_bottom = y + height

        intersecting: List[WorldMap] = []
        for world_map in self.maps:
            left, top, right, bottom = world_map.bounds
            # Strict inequalities so that edge/corner tangency does not
            # count as an intersection.
            if left < region_right and x < right and top < region_bottom and y < bottom:
                intersecting.append(world_map)

        return intersecting

    def load_world_map(
        self,
        world_map: WorldMap,
        encoding: str = "utf-8",
    ) -> "TiledMap":
        """Parse a single world map, caching the result.

        The map file is parsed with :func:`pytiled_parser.parse_map` on the
        first call and the resulting
        :class:`~pytiled_parser.tiled_map.TiledMap` is cached on this
        :class:`World`, so repeated calls for the same file (including the
        same file referenced by more than one :class:`WorldMap`) return the
        exact same object without parsing again.

        Args:
            world_map: The :class:`WorldMap` to load.
            encoding: The character encoding to use when opening the map
                file and any files it references.

        Returns:
            The parsed :class:`~pytiled_parser.tiled_map.TiledMap`.

        Raises:
            FileNotFoundError: If the map file does not exist. The error
                message includes the offending path.
            Exception: Any exception raised while parsing an existing file,
                such as :class:`~pytiled_parser.UnknownFormat`, is
                propagated unchanged.
        """
        map_path = Path(world_map.map_file).resolve()

        if map_path not in self._parsed_maps:
            if not map_path.is_file():
                raise FileNotFoundError(
                    f"Map file '{map_path}' referenced by world map at "
                    f"{world_map.coordinates!r} does not exist."
                )

            # Imported lazily to avoid a circular import between
            # pytiled_parser.world and pytiled_parser.parser.
            from pytiled_parser.parser import parse_map

            self._parsed_maps[map_path] = parse_map(map_path, encoding)

        return self._parsed_maps[map_path]

    def load_maps(
        self,
        x: float,
        y: float,
        width: float,
        height: float,
        encoding: str = "utf-8",
    ) -> List["TiledMap"]:
        """Query a rectangular region and parse the maps it covers.

        This combines :meth:`maps_in_region` with
        :meth:`load_world_map`. It returns a fully parsed
        :class:`~pytiled_parser.tiled_map.TiledMap` for each map
        intersecting the region, in world file order. Each distinct map
        file is parsed at most once across the lifetime of this
        :class:`World`; callers that wish to skip loading entirely can use
        :meth:`maps_in_region` on its own.

        Args:
            x: The x coordinate of the region's top-left corner in pixels.
            y: The y coordinate of the region's top-left corner in pixels.
            width: The width of the region in pixels. Must be greater than
                zero.
            height: The height of the region in pixels. Must be greater
                than zero.
            encoding: The character encoding to use when opening the map
                files and any files they reference.

        Returns:
            A list of parsed maps covering the region, in world file order.
            Duplicate references to the same file are collapsed to a single
            parsed map. An empty list means the region covered nothing.

        Raises:
            ValueError: If ``width`` or ``height`` is zero or negative.
            FileNotFoundError: If a referenced map file does not exist.
            Exception: Parsing errors from existing files are propagated.
        """
        world_maps = self.maps_in_region(x, y, width, height)

        parsed_maps: List[TiledMap] = []
        seen: Dict[Path, None] = {}
        for world_map in world_maps:
            map_path = Path(world_map.map_file).resolve()
            # Collapse duplicate references to the same file while keeping
            # the order of first appearance.
            if map_path in seen:
                continue
            seen[map_path] = None
            parsed_maps.append(self.load_world_map(world_map, encoding))

        return parsed_maps


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

    Raises:
        ValueError: If the declared width or height is zero or negative.
    """
    try:
        return WorldMap(
            map_file=map_file,
            size=Size(raw_world_map["width"], raw_world_map["height"]),
            coordinates=OrderedPair(raw_world_map["x"], raw_world_map["y"]),
        )
    except ValueError as err:
        raise ValueError(
            f"Invalid world map entry for file '{map_file}': {err}"
        ) from err


def parse_world(file: Path, encoding: str) -> World:
    """Parse the raw world into a pytiled_parser type

    Args:
        file: Path to the world's file

    Returns:
        World: A properly parsed [World][pytiled_parser.world.World]

    Raises:
        ValueError: If a map described by the world (either an explicit
            entry or one matched by a pattern) has a zero or negative
            width or height.
    """

    with open(file, encoding="utf-8") as world_file:
        raw_world = json.load(world_file)

    parent_dir = file.parent

    maps: List[WorldMap] = []

    if raw_world.get("maps"):
        for raw_map in raw_world["maps"]:
            map_path = Path(parent_dir / raw_map["fileName"])
            maps.append(_parse_world_map(raw_map, map_path))

    if raw_world.get("patterns"):
        for raw_pattern in raw_world["patterns"]:
            regex = re.compile(raw_pattern["regexp"])
            map_files = [
                f
                for f in listdir(parent_dir)
                if isfile(join(parent_dir, f)) and regex.match(f)
            ]
            for map_file in map_files:
                search = regex.search(map_file)
                if search:
                    width = raw_pattern["multiplierX"]
                    height = raw_pattern["multiplierY"]

                    offset_x = 0
                    offset_y = 0

                    if raw_pattern.get("offsetX"):
                        offset_x = raw_pattern["offsetX"]

                    if raw_pattern.get("offsetY"):
                        offset_y = raw_pattern["offsetY"]

                    x = (float(search.group(1)) * width) + offset_x
                    y = (float(search.group(2)) * height) + offset_y

                    raw_world_map: RawWorldMap = {
                        "fileName": map_file,
                        "width": width,
                        "height": height,
                        "x": x,
                        "y": y,
                    }

                    map_path = Path(parent_dir / map_file)
                    maps.append(_parse_world_map(raw_world_map, map_path))

    world = World(maps=maps)

    if raw_world.get("onlyShowAdjacentMaps"):
        world.only_show_adjacent = raw_world["onlyShowAdjacentMaps"]

    return world
