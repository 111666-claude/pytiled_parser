import json
import xml.etree.ElementTree as etree
from pathlib import Path
from typing import Optional, Union

from pytiled_parser import UnknownFormat
from pytiled_parser.exception import ProjectNotFoundError
from pytiled_parser.parsers.json.tiled_map import parse as json_map_parse
from pytiled_parser.parsers.json.tileset import parse as json_tileset_parse
from pytiled_parser.parsers.tmx.tiled_map import parse as tmx_map_parse
from pytiled_parser.parsers.tmx.tileset import parse as tmx_tileset_parse
from pytiled_parser.project import (
    Project,
    apply_project,
    discover_project,
    parse_project,
)
from pytiled_parser.tiled_map import TiledMap
from pytiled_parser.tileset import Tileset
from pytiled_parser.util import check_format
from pytiled_parser.world import World
from pytiled_parser.world import parse_world as _parse_world


def _resolve_project(
    project: Optional[Union[bool, Path, Project]], map_file: Path
) -> Optional[Project]:
    """Turn the public ``project`` argument into a loaded Project or None."""
    if project is None or project is False:
        return None
    if isinstance(project, Project):
        return project
    if project is True:
        project_file = discover_project(map_file.parent)
        if project_file is None:
            raise ProjectNotFoundError(
                f"Project support was requested for '{map_file}' but no "
                f"'.tiled-project' file was found in its directory "
                f"'{map_file.parent}'"
            )
        return parse_project(project_file)

    # Explicit path: the file must exist.
    return parse_project(Path(project))


def parse_map(
    file: Path,
    encoding: str = "utf-8",
    project: Optional[Union[bool, Path, Project]] = None,
) -> TiledMap:
    """Parse the raw Tiled map into a pytiled_parser type

    Args:
        file: Path to the map file
        encoding: The character encoding set to use when opening the file
        project: Controls loading of the Tiled project (``.tiled-project``)
            whose custom classes and enums define default property values:

            * ``None`` or ``False`` (default): no project is loaded and map
              files are parsed exactly as before.
            * ``True``: auto-discover a single ``.tiled-project`` file in the
              map's directory.
            * A :class:`~pytiled_parser.project.Project`: used directly.
            * A :class:`pathlib.Path`: loaded as a project file.

            Explicit property values always override same-named class defaults.

    Returns:
        TiledMap: A parsed and typed TiledMap
    """
    parser = check_format(file, encoding)

    # The type ignores are because mypy for some reason thinks those functions return Any
    if parser == "tmx":
        tiled_map = tmx_map_parse(file, encoding)  # type: ignore
    else:
        try:
            tiled_map = json_map_parse(file, encoding)  # type: ignore
        except ValueError:
            raise UnknownFormat(
                "Unknown Map Format, please use either the TMX or JSON format. "
                "This message could also mean your map file is invalid or corrupted."
            )

    loaded_project = _resolve_project(project, Path(file))
    if loaded_project is not None:
        apply_project(tiled_map, loaded_project)

    return tiled_map


def parse_tileset(
    file: Path,
    encoding: str = "utf-8",
    project: Optional[Union[bool, Path, Project]] = None,
) -> Tileset:
    """Parse the raw Tiled Tileset into a pytiled_parser type

    Args:
        file: Path to the map file
        encoding: The character encoding set to use when opening the file
        project: See :func:`parse_map`. When ``True`` the project is
            discovered in the tileset's directory.

    Returns:
        Tileset: A parsed and typed Tileset
    """
    parser = check_format(file, encoding)

    if parser == "tmx":
        with open(file, encoding=encoding) as map_file:
            raw_tileset = etree.parse(map_file).getroot()
        tileset = tmx_tileset_parse(raw_tileset, 1, encoding)
    else:
        try:
            with open(file, encoding=encoding) as my_file:
                raw_tileset = json.load(my_file)
            tileset = json_tileset_parse(raw_tileset, 1, encoding)
        except ValueError:
            raise UnknownFormat(
                "Unknowm Tileset Format, please use either the TSX or JSON format. "
                "This message could also mean your tileset file is invalid or corrupted."
            )

    loaded_project = _resolve_project(project, Path(file))
    if loaded_project is not None:
        apply_project(tileset, loaded_project, base_dir=Path(file).parent)

    return tileset


def parse_world(file: Path, encoding: str = "utf-8") -> World:
    """Parse the raw world file into a pytiled_parser type

    Args:
        file: Path to the world file
        encoding: The character encoding set to use when opening the file

    Returns:
        World: A parsed World
    """
    return _parse_world(file, encoding)
