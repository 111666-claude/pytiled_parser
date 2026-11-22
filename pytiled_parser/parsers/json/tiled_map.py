from pathlib import Path
from typing import Any, List, Optional, Union, cast

from typing_extensions import TypedDict

from pytiled_parser import diagnostics
from pytiled_parser.common_types import OrderedPair, Size
from pytiled_parser.diagnostics import (
    JsonDocument,
    JsonPath,
    XmlDocument,
    format_path,
)
from pytiled_parser.exception import (
    FieldTypeError,
    InvalidFieldValueError,
    UnrecognizedFormat,
)
from pytiled_parser.parsers.json.layer import RawLayer
from pytiled_parser.parsers.json.layer import parse as parse_layer
from pytiled_parser.parsers.json.properties import RawProperty
from pytiled_parser.parsers.json.properties import parse as parse_properties
from pytiled_parser.parsers.json.tileset import RawTileSet
from pytiled_parser.parsers.json.tileset import parse as parse_json_tileset
from pytiled_parser.parsers.tmx.tileset import parse as parse_tmx_tileset
from pytiled_parser.tiled_map import TiledMap, TilesetDict
from pytiled_parser.util import parse_color

RawTilesetMapping = TypedDict("RawTilesetMapping", {"firstgid": int, "source": str})


RawTiledMap = TypedDict(
    "RawTiledMap",
    {
        "backgroundcolor": str,
        "compressionlevel": int,
        "height": int,
        "hexsidelength": int,
        "infinite": bool,
        "layers": List[RawLayer],
        "nextlayerid": int,
        "nextobjectid": int,
        "orientation": str,
        "properties": List[RawProperty],
        "renderorder": str,
        "staggeraxis": str,
        "staggerindex": str,
        "tiledversion": str,
        "tileheight": int,
        "tilesets": List[RawTilesetMapping],
        "tilewidth": int,
        "class": str,
        "type": str,
        "version": Union[str, float],
        "width": int,
        "parallaxoriginx": float,
        "parallaxoriginy": float,
    },
)
RawTiledMap.__doc__ = """
    The keys and their types that appear in a Tiled JSON Map Object.

    Tiled Docs: https://doc.mapeditor.org/en/stable/reference/json-map-format/#map
"""


def _parse_external_tileset(
    raw_tileset: RawTilesetMapping,
    parent_dir: Path,
    *,
    path: JsonPath,
) -> Any:
    source = cast(str, diagnostics.json_require(raw_tileset, "source", str, path=path))
    tileset_path = Path(parent_dir / source)
    display = diagnostics.display_reference(source, parent_dir)

    diagnostics.check_external_file(
        tileset_path,
        source,
        base_dir=parent_dir,
        json_path=path + ("source",),
    )

    document = diagnostics.load_document(tileset_path, display=display)

    firstgid = cast(
        int, diagnostics.json_require(raw_tileset, "firstgid", int, path=path)
    )

    referencing_file = diagnostics.current_frame().document
    with diagnostics.enter(
        document=document,
        parent_dir=tileset_path.parent,
        referenced_from="{}: {}".format(
            (
                referencing_file.display_file
                if isinstance(referencing_file, JsonDocument)
                else display
            ),
            format_path(path + ("source",)),
        ),
    ):
        if isinstance(document, JsonDocument):
            return parse_json_tileset(
                document.data, firstgid, external_path=tileset_path.parent
            )
        assert isinstance(document, XmlDocument)
        return parse_tmx_tileset(
            document.root, firstgid, external_path=tileset_path.parent
        )


def parse(file: Path) -> TiledMap:
    """Parse the raw Tiled map into a pytiled_parser type.

    Args:
        file: Path to the map file.

    Returns:
        TiledMap: A parsed TiledMap.
    """
    document = diagnostics.load_document(file)
    if not isinstance(document, JsonDocument):
        raise UnrecognizedFormat(
            "Expected a JSON map file, but '{}' contains XML".format(
                document.display_file
            ),
            file=document.display_file,
            reason="file content does not match its detected format",
        )

    raw_tiled_map = document.data

    if not isinstance(raw_tiled_map, dict):
        raise diagnostics.json_error(
            FieldTypeError,
            "Expected the map to be a JSON object, got {}".format(
                type(raw_tiled_map).__name__
            ),
            reason="maps are JSON objects",
        )

    with diagnostics.enter(document=document, parent_dir=file.parent):
        return _parse_map(
            file,
            raw_tiled_map,  # type: ignore[arg-type]
            document,
        )


def _parse_map(
    file: Path, raw_tiled_map: RawTiledMap, document: JsonDocument
) -> TiledMap:
    parent_dir = file.parent

    raw_tilesets = diagnostics.json_require(raw_tiled_map, "tilesets", list)
    tilesets: TilesetDict = {}

    for index, raw_tileset in enumerate(cast(List[Any], raw_tilesets)):
        tileset_path = ("tilesets", index)
        if not isinstance(raw_tileset, dict):
            raise diagnostics.json_error(
                FieldTypeError,
                "Expected an object for tileset at index {}, got {}".format(
                    index, type(raw_tileset).__name__
                ),
                path=tileset_path,
                reason="tileset references are JSON objects",
            )
        if raw_tileset.get("source") is not None:
            tilesets[
                cast(
                    int,
                    diagnostics.json_require(
                        raw_tileset, "firstgid", int, path=tileset_path
                    ),
                )
            ] = _parse_external_tileset(
                raw_tileset,  # type: ignore[arg-type]
                parent_dir,
                path=tileset_path,
            )
        else:
            # Is an embedded Tileset
            embedded = cast(RawTileSet, raw_tileset)
            firstgid = cast(
                int,
                diagnostics.json_require(embedded, "firstgid", int, path=tileset_path),
            )
            tilesets[firstgid] = parse_json_tileset(
                embedded, firstgid, path=tileset_path
            )

    version_value = diagnostics.json_require(raw_tiled_map, "version", (str, float))
    if isinstance(version_value, float):  # pragma: no cover
        version = str(version_value)
    else:
        version = str(version_value)

    width = cast(int, diagnostics.json_require(raw_tiled_map, "width", int))
    height = cast(int, diagnostics.json_require(raw_tiled_map, "height", int))
    tilewidth = cast(int, diagnostics.json_require(raw_tiled_map, "tilewidth", int))
    tileheight = cast(int, diagnostics.json_require(raw_tiled_map, "tileheight", int))

    raw_layers = diagnostics.json_require(raw_tiled_map, "layers", list)
    parsed_layers = []
    for index, layer_ in enumerate(cast(List[RawLayer], raw_layers)):
        parsed_layers.append(
            parse_layer(layer_, parent_dir, path=("layers", index), index=index)
        )

    # `map` is a built-in function
    map_ = TiledMap(
        map_file=file,
        infinite=cast(
            bool,
            diagnostics.json_optional(raw_tiled_map, "infinite", bool, default=False),
        ),
        layers=parsed_layers,
        map_size=Size(width, height),
        next_layer_id=cast(
            Optional[int],
            diagnostics.json_optional(raw_tiled_map, "nextlayerid", int),
        ),
        next_object_id=cast(
            int, diagnostics.json_require(raw_tiled_map, "nextobjectid", int)
        ),
        orientation=cast(
            str, diagnostics.json_require(raw_tiled_map, "orientation", str)
        ),
        render_order=cast(
            str, diagnostics.json_require(raw_tiled_map, "renderorder", str)
        ),
        tiled_version=cast(
            str,
            diagnostics.json_optional(raw_tiled_map, "tiledversion", str, default=""),
        ),
        tile_size=Size(tilewidth, tileheight),
        tilesets=tilesets,
        version=version,
    )

    layers = [layer for layer in map_.layers if hasattr(layer, "tiled_objects")]

    for my_layer in layers:
        # Mypy extremely hates what is going on in this whole block.
        # This is because we are doing some run-time modification of the
        # attributes on the tiled_object class, to support tilesets which
        # were defined in a Tiled Object Template.
        for tiled_object in my_layer.tiled_objects:  # type: ignore
            if hasattr(tiled_object, "new_tileset"):
                if tiled_object.new_tileset is not None:
                    already_loaded = None
                    for val in map_.tilesets.values():
                        if val.name == tiled_object.new_tileset["name"]:
                            already_loaded = val
                            break

                    if not already_loaded:
                        highest_firstgid = max(map_.tilesets.keys())
                        last_tileset_count = map_.tilesets[highest_firstgid].tile_count
                        new_firstgid = highest_firstgid + last_tileset_count
                        map_.tilesets[new_firstgid] = parse_json_tileset(
                            tiled_object.new_tileset,
                            new_firstgid,
                            tiled_object.new_tileset_path,
                        )
                        tiled_object.gid = tiled_object.gid + (new_firstgid - 1)

                    else:
                        tiled_object.gid = tiled_object.gid + (
                            already_loaded.firstgid - 1
                        )

                    tiled_object.new_tileset = None
                    tiled_object.new_tileset_path = None

    if raw_tiled_map.get("class") is not None:
        map_.class_ = cast(str, diagnostics.json_optional(raw_tiled_map, "class", str))

    if raw_tiled_map.get("backgroundcolor") is not None:
        background = cast(
            str,
            diagnostics.json_optional(raw_tiled_map, "backgroundcolor", str),
        )
        try:
            map_.background_color = parse_color(background)
        except InvalidFieldValueError as error:
            raise diagnostics.json_error(
                InvalidFieldValueError,
                "Invalid background color {!r}".format(background),
                path=("backgroundcolor",),
                reason=error.reason or str(error),
            ) from error

    if raw_tiled_map.get("hexsidelength") is not None:
        map_.hex_side_length = cast(
            int,
            diagnostics.json_optional(raw_tiled_map, "hexsidelength", int),
        )

    if raw_tiled_map.get("properties") is not None:
        properties = diagnostics.json_require(raw_tiled_map, "properties", list)
        map_.properties = parse_properties(properties, path=("properties",))

    if raw_tiled_map.get("staggeraxis") is not None:
        map_.stagger_axis = cast(
            str,
            diagnostics.json_optional(raw_tiled_map, "staggeraxis", str),
        )

    if raw_tiled_map.get("staggerindex") is not None:
        map_.stagger_index = cast(
            str,
            diagnostics.json_optional(raw_tiled_map, "staggerindex", str),
        )

    _parallax_origin_x: float = 0
    _parallax_origin_y: float = 0

    if raw_tiled_map.get("parallaxoriginx") is not None:
        _parallax_origin_x = cast(
            float,
            diagnostics.json_optional(raw_tiled_map, "parallaxoriginx", (int, float)),
        )

    if raw_tiled_map.get("parallaxoriginy") is not None:
        _parallax_origin_y = cast(
            float,
            diagnostics.json_optional(raw_tiled_map, "parallaxoriginy", (int, float)),
        )

    map_.parallax_origin = OrderedPair(_parallax_origin_x, _parallax_origin_y)

    return map_
