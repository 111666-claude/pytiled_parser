from pathlib import Path

from pytiled_parser import diagnostics
from pytiled_parser.diagnostics import JsonDocument, XmlDocument
from pytiled_parser.parsers.json.tiled_map import parse as json_map_parse
from pytiled_parser.parsers.json.tileset import parse as json_tileset_parse
from pytiled_parser.parsers.tmx.tiled_map import parse as tmx_map_parse
from pytiled_parser.parsers.tmx.tileset import parse as tmx_tileset_parse
from pytiled_parser.tiled_map import TiledMap
from pytiled_parser.tileset import Tileset
from pytiled_parser.util import check_format, load_document
from pytiled_parser.world import World
from pytiled_parser.world import parse_world as _parse_world


def parse_map(file: Path) -> TiledMap:
    """Parse the raw Tiled map into a pytiled_parser type

    Args:
        file: Path to the map file

    Returns:
        TiledMap: A parsed and typed TiledMap

    Raises:
        UnknownFormat: When the file is not a recognized TMX/JSON map.  The
            concrete subclass distinguishes a genuinely unknown format
            (:class:`UnrecognizedFormat`) from a syntax error inside a
            recognized one (:class:`FormatSyntaxError`).
        ParseError: For type/field errors in a recognized map.  These never
            masquerade as :class:`UnknownFormat`.
    """
    parser = check_format(file)

    # The type ignores are because mypy for some reason thinks those functions return Any
    if parser == "tmx":
        return tmx_map_parse(file)  # type: ignore
    return json_map_parse(file)  # type: ignore


def parse_tileset(file: Path) -> Tileset:
    """Parse the raw Tiled Tileset into a pytiled_parser type

    Args:
        file: Path to the map file

    Returns:
        Tileset: A parsed and typed Tileset
    """
    parser = check_format(file)

    if parser == "tmx":
        document = load_document(file)
        assert isinstance(document, XmlDocument)
        with diagnostics.enter(document=document, parent_dir=file.parent):
            return tmx_tileset_parse(document.root, 1)

    document = load_document(file)
    assert isinstance(document, JsonDocument)
    with diagnostics.enter(document=document, parent_dir=file.parent):
        return json_tileset_parse(document.data, 1)


def parse_world(file: Path) -> World:
    """Parse the raw world file into a pytiled_parser type

    Args:
        file: Path to the world file

    Returns:
        World: A parsed and typed World
    """
    return _parse_world(file)
