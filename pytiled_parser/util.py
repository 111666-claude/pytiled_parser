"""Utility Functions for PyTiled"""

import contextvars
import copy
import json
import xml.etree.ElementTree as etree
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, Optional, Tuple

from pytiled_parser.common_types import Color


def parse_color(color: str) -> Color:
    """Convert Tiled color format into PyTiled's.

    Tiled's color format is #AARRGGBB and PyTiled's is an RGBA tuple.

    Args:
        color (str): Tiled formatted color string.
    Returns:
        :Color: Color object in the format that PyTiled understands.
    """
    # the actual part we care about is always an even number
    if len(color) % 2:
        # strip initial '#' character
        color = color[1:]

    if len(color) == 6:
        # full opacity if no alpha specified
        return Color(int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16), 255)
    elif len(color) == 8:
        return Color(
            int(color[2:4], 16),
            int(color[4:6], 16),
            int(color[6:8], 16),
            int(color[0:2], 16),
        )

    raise ValueError("Improperly formatted color passed to parse_color")


def check_format(file_path: Path, encoding: str) -> str:
    with open(file_path, encoding=encoding) as file:
        line = file.readline().rstrip().strip()
        if line[0] == "<":
            return "tmx"
        else:
            return "json"


# --- Per-call external resource cache -------------------------------------
#
# Maps (resolved path, encoding) -> raw parsed content (etree.Element for
# TMX/TSX/TX files, dict for JSON). The cache lives in a ContextVar that is
# only set for the duration of a single top-level parse call (see
# resource_cache_scope), so repeated references to the same external
# template/tileset within one parse_map call are opened and parsed exactly
# once, while separate calls never share stale state.

_ResourceCacheKey = Tuple[str, str]

# Sentinels for cache entries.
_IN_PROGRESS = object()
_MISSING = object()

_resource_cache: contextvars.ContextVar[Optional[Dict[_ResourceCacheKey, Any]]] = (
    contextvars.ContextVar("pytiled_parser_resource_cache", default=None)
)


@contextmanager
def resource_cache_scope() -> Iterator[None]:
    """Provide an isolated raw-resource cache for one top-level parse call.

    Nested scopes (e.g. a parse call made from within another parse) each get
    their own fresh cache; the previous one is restored on exit.
    """
    token = _resource_cache.set({})
    try:
        yield
    finally:
        _resource_cache.reset(token)


def _read_raw_resource(file_path: Path, encoding: str) -> Any:
    """Open and parse an external resource file exactly once.

    Returns the root etree.Element for XML files, or the decoded dict for
    JSON files. Errors are re-raised with the file path attached so failures
    keep their context.
    """
    try:
        with open(file_path, encoding=encoding) as resource_file:
            first_line = resource_file.readline()
            resource_file.seek(0)
            if first_line.rstrip().strip().startswith("<"):
                return etree.parse(resource_file).getroot()
            return json.load(resource_file)
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            f"External resource not found while parsing: {file_path}"
        ) from exc
    except etree.ParseError as exc:
        raise etree.ParseError(
            f"Invalid XML in external resource {file_path}: {exc}"
        ) from exc
    except json.JSONDecodeError as exc:
        # Re-raised as ValueError (JSONDecodeError's parent) so existing
        # handlers that catch ValueError keep working.
        raise ValueError(
            f"Invalid JSON in external resource {file_path}: {exc}"
        ) from exc


def load_raw_resource(file_path: Path, encoding: str) -> Any:
    """Load the raw parsed content of an external resource, cached per call.

    The cache key is the canonical (resolved) path plus the encoding, so the
    same file referenced through different relative spellings is only read
    once. Without an active resource_cache_scope no caching happens.

    The returned object is shared and must be treated as read-only; callers
    that need to mutate the result must make their own copy.

    Raises:
        RuntimeError: If loading this resource re-entrantly requires loading
            the same resource (circular reference).
    """
    key = (str(Path(file_path).resolve()), encoding)
    cache = _resource_cache.get()
    if cache is None:
        return _read_raw_resource(file_path, encoding)

    cached = cache.get(key, _MISSING)
    if cached is _IN_PROGRESS:
        raise RuntimeError(
            "Circular reference detected while parsing external resource: "
            f"{file_path}"
        )
    if cached is not _MISSING:
        return cached

    cache[key] = _IN_PROGRESS
    try:
        value = _read_raw_resource(file_path, encoding)
    except BaseException:
        # Failures are not cached; each reference re-attempts the load and
        # re-raises the contextual error.
        del cache[key]
        raise
    cache[key] = value
    return value


def load_object_template(file_path: Path, encoding: str) -> Any:
    # The raw template is shared through the cache; deep-copy it because the
    # object parsers merge each map object's overrides into the template
    # content, and every referencing object must get an independent copy.
    template = copy.deepcopy(load_raw_resource(file_path, encoding))

    new_tileset = None
    new_tileset_path = None

    if isinstance(template, etree.Element):
        tileset_element = template.find("./tileset")
        if tileset_element is not None:
            tileset_path = Path(file_path.parent / tileset_element.attrib["source"])
            new_tileset = load_object_tileset(tileset_path, encoding)
            new_tileset_path = tileset_path.parent
    else:
        if "tileset" in template:
            tileset_path = Path(file_path.parent / template["tileset"]["source"])  # type: ignore
            new_tileset = load_object_tileset(tileset_path, encoding)
            new_tileset_path = tileset_path.parent

    return (template, new_tileset, new_tileset_path)


def load_object_tileset(file_path: Path, encoding: str) -> Any:
    # Deep-copied for the same reason as templates: the raw content is cached
    # and shared, but each template instance must not observe another
    # instance's mutations.
    return copy.deepcopy(load_raw_resource(file_path, encoding))
