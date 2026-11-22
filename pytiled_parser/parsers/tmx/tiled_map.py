import xml.etree.ElementTree as etree
from pathlib import Path
from typing import Any

from pytiled_parser import diagnostics
from pytiled_parser.common_types import OrderedPair, Size
from pytiled_parser.diagnostics import JsonDocument, XmlDocument
from pytiled_parser.exception import (
    InvalidFieldValueError,
    UnrecognizedFormat,
)
from pytiled_parser.parsers.json.tileset import parse as parse_json_tileset
from pytiled_parser.parsers.tmx.layer import parse as parse_layer
from pytiled_parser.parsers.tmx.properties import parse as parse_properties
from pytiled_parser.parsers.tmx.tileset import parse as parse_tmx_tileset
from pytiled_parser.tiled_map import TiledMap, TilesetDict
from pytiled_parser.util import parse_color

_LAYER_TAGS = ("layer", "objectgroup", "imagelayer", "group")


def _parse_external_tileset(raw_tileset: etree.Element, parent_dir: Path) -> Any:
    source = diagnostics.xml_attr(raw_tileset, "source", field="tileset.source")
    assert source is not None
    tileset_path = Path(parent_dir / source)
    firstgid = diagnostics.xml_int_attr(raw_tileset, "firstgid", field="tileset")

    diagnostics.check_external_file(
        tileset_path,
        source,
        base_dir=parent_dir,
        element=raw_tileset,
        field="tileset.source",
    )

    document = diagnostics.load_document(
        tileset_path,
        display=diagnostics.display_reference(source, parent_dir),
    )

    referencing_file = diagnostics.current_frame().document
    with diagnostics.enter(
        document=document,
        parent_dir=tileset_path.parent,
        referenced_from="{}: {}".format(
            (
                referencing_file.display_file
                if isinstance(referencing_file, XmlDocument)
                else diagnostics.display_reference(source, parent_dir)
            ),
            "tileset.source",
        ),
    ):
        if isinstance(document, XmlDocument):
            return parse_tmx_tileset(
                document.root, firstgid, external_path=tileset_path.parent
            )
        assert isinstance(document, JsonDocument)
        return parse_json_tileset(
            document.data, firstgid, external_path=tileset_path.parent
        )


def parse(file: Path) -> TiledMap:
    """Parse the raw Tiled map into a pytiled_parser type.

    Args:
        file: Path to the map file.

    Returns:
        TiledMap: A parsed TiledMap.
    """
    document = diagnostics.load_document(file)
    if not isinstance(document, XmlDocument):
        raise UnrecognizedFormat(
            "Expected a TMX map file, but '{}' contains JSON".format(
                document.display_file
            ),
            file=document.display_file,
            reason="file content does not match its detected format",
        )

    parent_dir = file.parent

    with diagnostics.enter(document=document, parent_dir=parent_dir):
        return _parse_map(file, document.root, document)


def _parse_map(file: Path, raw_map: etree.Element, document: XmlDocument) -> TiledMap:
    parent_dir = file.parent

    raw_tilesets = raw_map.findall("./tileset")
    tilesets: TilesetDict = {}

    for raw_tileset in raw_tilesets:
        if raw_tileset.attrib.get("source") is not None:
            firstgid = diagnostics.xml_int_attr(
                raw_tileset, "firstgid", field="tileset"
            )
            tilesets[firstgid] = _parse_external_tileset(raw_tileset, parent_dir)
        else:
            # Is an embedded Tileset
            firstgid = diagnostics.xml_int_attr(
                raw_tileset, "firstgid", field="tileset"
            )
            tilesets[firstgid] = parse_tmx_tileset(raw_tileset, firstgid)

    layers = []
    for index, element in enumerate(raw_map.findall("./*")):
        if element.tag in _LAYER_TAGS:
            layers.append(parse_layer(element, parent_dir, index=index))

    map_ = TiledMap(
        map_file=file,
        infinite=diagnostics.xml_bool_attr(raw_map, "infinite"),
        layers=layers,
        map_size=Size(
            diagnostics.xml_int_attr(raw_map, "width"),
            diagnostics.xml_int_attr(raw_map, "height"),
        ),
        next_layer_id=diagnostics.xml_int_attr(raw_map, "nextlayerid"),
        next_object_id=diagnostics.xml_int_attr(raw_map, "nextobjectid"),
        orientation=diagnostics.xml_attr(raw_map, "orientation"),
        render_order=diagnostics.xml_attr(raw_map, "renderorder"),
        tiled_version=diagnostics.xml_attr(raw_map, "tiledversion"),
        tile_size=Size(
            diagnostics.xml_int_attr(raw_map, "tilewidth"),
            diagnostics.xml_int_attr(raw_map, "tileheight"),
        ),
        tilesets=tilesets,
        version=diagnostics.xml_attr(raw_map, "version"),
    )

    layers = [layer for layer in map_.layers if hasattr(layer, "tiled_objects")]

    for my_layer in layers:
        # This is runtime modification of TiledObject attributes to support
        # tilesets defined in an Object Template.
        for tiled_object in my_layer.tiled_objects:  # type: ignore
            if hasattr(tiled_object, "new_tileset"):
                if tiled_object.new_tileset is not None:
                    already_loaded = None
                    for val in map_.tilesets.values():
                        if val.name == tiled_object.new_tileset.attrib["name"]:
                            already_loaded = val
                            break

                    if not already_loaded:
                        highest_firstgid = max(map_.tilesets.keys())
                        last_tileset_count = map_.tilesets[highest_firstgid].tile_count
                        new_firstgid = highest_firstgid + last_tileset_count
                        map_.tilesets[new_firstgid] = parse_tmx_tileset(
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

    if raw_map.attrib.get("backgroundcolor") is not None:
        background = raw_map.attrib["backgroundcolor"]
        try:
            map_.background_color = parse_color(background)
        except InvalidFieldValueError as error:
            raise diagnostics.xml_error(
                InvalidFieldValueError,
                "Invalid background color {!r}".format(background),
                element=raw_map,
                field="backgroundcolor",
                reason=error.reason or str(error),
            ) from error

    if raw_map.attrib.get("hexsidelength") is not None:
        map_.hex_side_length = diagnostics.xml_int_attr(  # type: ignore[assignment]
            raw_map, "hexsidelength"
        )

    properties_element = raw_map.find("./properties")
    if properties_element is not None:
        map_.properties = parse_properties(properties_element)

    if raw_map.attrib.get("staggeraxis") is not None:
        map_.stagger_axis = raw_map.attrib["staggeraxis"]

    if raw_map.attrib.get("staggerindex") is not None:
        map_.stagger_index = raw_map.attrib["staggerindex"]

    if raw_map.attrib.get("class") is not None:
        map_.class_ = raw_map.attrib["class"]

    _parallax_origin_x = 0.0
    _parallax_origin_y = 0.0

    if raw_map.attrib.get("parallaxoriginx") is not None:
        _parallax_origin_x = diagnostics.xml_float_attr(  # type: ignore[assignment]
            raw_map, "parallaxoriginx"
        )

    if raw_map.attrib.get("parallaxoriginy") is not None:
        _parallax_origin_y = diagnostics.xml_float_attr(  # type: ignore[assignment]
            raw_map, "parallaxoriginy"
        )

    map_.parallax_origin = OrderedPair(_parallax_origin_x, _parallax_origin_y)

    return map_
