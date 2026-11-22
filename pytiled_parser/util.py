"""Utility Functions for PyTiled"""

from pathlib import Path
from typing import Any, Optional, Tuple

from pytiled_parser import diagnostics
from pytiled_parser.common_types import Color
from pytiled_parser.diagnostics import XmlDocument
from pytiled_parser.exception import (
    ExternalFileError,
    ExternalFileNotFoundError,
    InvalidFieldValueError,
    UnrecognizedFormat,
)


def parse_color(color: str) -> Color:
    """Convert Tiled color format into PyTiled's.

    Tiled's color format is #AARRGGBB and PyTiled's is an RGBA tuple.

    Args:
        color (str): Tiled formatted color string.
    Returns:
        :Color: Color object in the format that PyTiled understands.

    Raises:
        InvalidFieldValueError: If the string is not a Tiled color.
    """
    original = color
    if not isinstance(color, str):
        raise InvalidFieldValueError(
            "Color must be a string, got {}".format(type(color).__name__),
            reason="colors are strings such as '#AARRGGBB'",
        )

    # the actual part we care about is always an even number
    if len(color) % 2:
        # strip initial '#' character
        color = color[1:]

    if len(color) == 6:
        # full opacity if no alpha specified
        try:
            return Color(
                int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16), 255
            )
        except ValueError:
            pass
    elif len(color) == 8:
        try:
            return Color(
                int(color[2:4], 16),
                int(color[4:6], 16),
                int(color[6:8], 16),
                int(color[0:2], 16),
            )
        except ValueError:
            pass

    raise InvalidFieldValueError(
        "Improperly formatted color: {!r}".format(original),
        reason="expected '#RGB', '#RRGGBB' or '#AARRGGBB'",
    )


def _sniff_format(prefix: str) -> str:
    """Identify a format from the leading text of a file.

    This never indexes into an empty string and tolerates leading whitespace
    and a UTF-8 BOM, which are all common in hand-edited files.
    """

    stripped = prefix.lstrip("﻿ \t\r\n")
    if not stripped:
        return "empty"
    if stripped[0] == "<":
        return "tmx"
    if stripped[0] in "{[":
        return "json"
    return "unknown"


def check_format(file_path: Path) -> str:
    """Return ``"tmx"`` or ``"json"`` for a file, or raise a precise error."""

    try:
        with open(file_path, "rb") as file:
            prefix = file.read(4096).decode("utf-8", "replace")
    except FileNotFoundError as error:
        display = diagnostics.display_file(file_path)
        raise ExternalFileNotFoundError(
            "Map file not found: '{}'".format(display),
            file=display,
            reason="no such file: {}".format(display),
        ) from error
    except IsADirectoryError as error:
        display = diagnostics.display_file(file_path)
        raise ExternalFileError(
            "Expected a file but found a directory: '{}'".format(display),
            file=display,
            reason="path is a directory",
        ) from error
    except OSError as error:
        display = diagnostics.display_file(file_path)
        raise ExternalFileError(
            "Could not read '{}'".format(display),
            file=display,
            reason=str(error),
        ) from error

    detected = _sniff_format(prefix)
    if detected == "tmx":
        return "tmx"
    if detected == "json":
        return "json"

    display = diagnostics.display_file(file_path)
    if detected == "empty":
        message = "File '{}' is empty, expected a TMX or JSON map/tileset".format(
            display
        )
        reason = "file is empty"
    else:
        message = "Unknown format for '{}': expected a TMX/XML or JSON file".format(
            display
        )
        reason = "first character is neither '<' nor '{'"
    raise UnrecognizedFormat(message, file=display, reason=reason)


def load_document(file: Path, *, display: Optional[str] = None) -> Any:
    """Read *file* and return a format specific source document.

    Returns either a :class:`~pytiled_parser.diagnostics.JsonDocument` or an
    :class:`~pytiled_parser.diagnostics.XmlDocument`.  Both expose the parsed
    content and can resolve elements/paths to source positions.
    """

    return diagnostics.load_document(file, display=display)


def load_object_template(
    file_path: Path, display: Optional[str] = None
) -> Tuple[Any, Any, Optional[Path]]:
    """Load an object template and its optional tileset.

    Returns a tuple of ``(template, tileset, tileset_path)``.  Template
    documents keep their format specific representation (an ``Element`` for
    TMX, a ``dict`` for JSON) for backwards compatibility.
    """

    if display is None:
        display = diagnostics.display_file(file_path)

    document = load_document(file_path, display=display)

    new_tileset = None
    new_tileset_path = None

    if isinstance(document, XmlDocument):
        template = document.root
        tileset_element = template.find("./tileset")
        if tileset_element is not None:
            source = diagnostics.xml_attr(
                tileset_element, "source", field="tileset.source"
            )
            tileset_path = Path(file_path.parent / source)
            new_tileset = load_object_tileset(tileset_path, base_dir=file_path.parent)
            new_tileset_path = tileset_path.parent
    else:
        template = document.data
        if isinstance(template, dict) and template.get("tileset") is not None:
            tileset_info = template["tileset"]
            source = diagnostics.json_require(
                tileset_info, "source", str, path=("tileset",)
            )
            tileset_path = Path(file_path.parent / source)
            new_tileset = load_object_tileset(tileset_path, base_dir=file_path.parent)
            new_tileset_path = tileset_path.parent

    return (template, new_tileset, new_tileset_path)


def load_object_tileset(
    file_path: Path,
    base_dir: Optional[Path] = None,
) -> Any:
    """Load a tileset referenced by an object template.

    Tile set files reached from a template are displayed relative to
    ``base_dir`` so error messages never expose unrelated absolute paths.
    """

    document = load_document(
        file_path, display=diagnostics.display_file(file_path, base_dir)
    )
    if isinstance(document, XmlDocument):
        return document.root
    return document.data
