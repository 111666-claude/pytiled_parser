import json
import xml.etree.ElementTree as etree
from pathlib import Path
from typing import Optional

from pytiled_parser import UnknownFormat
from pytiled_parser.parsers.json.tiled_map import parse as json_map_parse
from pytiled_parser.parsers.json.tileset import parse as json_tileset_parse
from pytiled_parser.parsers.tmx.tiled_map import parse as tmx_map_parse
from pytiled_parser.parsers.tmx.tileset import parse as tmx_tileset_parse
from pytiled_parser.sandbox import PathSandbox
from pytiled_parser.tiled_map import TiledMap
from pytiled_parser.tileset import Tileset
from pytiled_parser.util import check_format
from pytiled_parser.world import World
from pytiled_parser.world import parse_world as _parse_world


def parse_map(file: Path, sandbox: Optional[PathSandbox] = None) -> TiledMap:
    """Parse the raw Tiled map into a pytiled_parser type

    Args:
        file: Path to the map file
        sandbox: Optional :class:`~pytiled_parser.sandbox.PathSandbox`. When
            provided, the map file and every recursive reference (tilesets,
            templates, images) is checked against the sandbox before it is
            opened. When omitted the original unrestricted behavior applies.

    Returns:
        TiledMap: A parsed and typed TiledMap
    """
    if sandbox is not None:
        file = sandbox.check_path(file, context="map file")

    parser = check_format(file)

    # The type ignores are because mypy for some reason thinks those functions return Any
    if parser == "tmx":
        return tmx_map_parse(file, sandbox=sandbox)  # type: ignore
    else:
        try:
            return json_map_parse(file, sandbox=sandbox)  # type: ignore
        except ValueError:
            raise UnknownFormat(
                "Unknown Map Format, please use either the TMX or JSON format. "
                "This message could also mean your map file is invalid or corrupted."
            )


def parse_tileset(file: Path, sandbox: Optional[PathSandbox] = None) -> Tileset:
    """Parse the raw Tiled Tileset into a pytiled_parser type

    Args:
        file: Path to the map file
        sandbox: Optional :class:`~pytiled_parser.sandbox.PathSandbox`. When
            provided, the tileset file and any image references within it are
            checked against the sandbox before they are opened.

    Returns:
        Tileset: A parsed and typed Tileset
    """
    if sandbox is not None:
        file = sandbox.check_path(file, context="tileset file")

    parser = check_format(file)

    if parser == "tmx":
        with open(file) as map_file:
            raw_tileset = etree.parse(map_file).getroot()
        return tmx_tileset_parse(
            raw_tileset,
            1,
            external_path=file.parent if sandbox is not None else None,
            sandbox=sandbox,
        )
    else:
        try:
            with open(file) as my_file:
                raw_tileset = json.load(my_file)
            return json_tileset_parse(
                raw_tileset,
                1,
                external_path=file.parent if sandbox is not None else None,
                sandbox=sandbox,
            )
        except ValueError:
            raise UnknownFormat(
                "Unknowm Tileset Format, please use either the TSX or JSON format. "
                "This message could also mean your tileset file is invalid or corrupted."
            )


def parse_world(file: Path, sandbox: Optional[PathSandbox] = None) -> World:
    """Parse the raw world file into a pytiled_parser type

    Args:
        file: Path to the world file
        sandbox: Optional :class:`~pytiled_parser.sandbox.PathSandbox`. When
            provided, the world file and each map referenced by it is checked
            against the sandbox. The individual maps are not parsed here, but
            their recorded paths are guaranteed to lie within the sandbox.

    Returns:
        World: A parsed and typed World
    """
    return _parse_world(file, sandbox=sandbox)
