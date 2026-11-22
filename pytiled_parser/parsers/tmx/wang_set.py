import xml.etree.ElementTree as etree

from pytiled_parser import diagnostics
from pytiled_parser.exception import FieldTypeError, InvalidFieldValueError
from pytiled_parser.parsers.tmx.properties import parse as parse_properties
from pytiled_parser.util import parse_color
from pytiled_parser.wang_set import WangColor, WangSet, WangTile


def _parse_wang_tile(raw_wang_tile: etree.Element) -> WangTile:
    raw_ids = diagnostics.xml_attr(raw_wang_tile, "wangid", field="wangtile.wangid")
    assert raw_ids is not None

    ids = []
    for index, value in enumerate(raw_ids.split(",")):
        try:
            ids.append(int(value.strip()))
        except (TypeError, ValueError) as error:
            raise diagnostics.xml_error(
                FieldTypeError,
                "Expected an integer in wangid at index {}, got {!r}".format(
                    index, value.strip()
                ),
                element=raw_wang_tile,
                field="wangtile.wangid[{}]".format(index),
                reason=str(error),
            ) from error

    return WangTile(
        tile_id=diagnostics.xml_int_attr(raw_wang_tile, "tileid", field="wangtile"),
        wang_id=ids,
    )


def _parse_wang_color(raw_wang_color: etree.Element) -> WangColor:
    color = diagnostics.xml_attr(raw_wang_color, "color", field="wangcolor")
    assert color is not None

    try:
        parsed_color = parse_color(color)
    except InvalidFieldValueError as error:
        raise diagnostics.xml_error(
            InvalidFieldValueError,
            "Invalid wang color {!r}".format(color),
            element=raw_wang_color,
            field="wangcolor.color",
            reason=error.reason or str(error),
        ) from error

    wang_color = WangColor(
        name=diagnostics.xml_attr(raw_wang_color, "name", field="wangcolor"),
        color=parsed_color,
        tile=diagnostics.xml_int_attr(raw_wang_color, "tile", field="wangcolor"),
        probability=diagnostics.xml_float_attr(
            raw_wang_color, "probability", field="wangcolor"
        ),
    )

    if raw_wang_color.attrib.get("class") is not None:
        wang_color.class_ = raw_wang_color.attrib["class"]

    properties = raw_wang_color.find("./properties")
    if properties is not None:
        wang_color.properties = parse_properties(properties)

    return wang_color


def parse(raw_wangset: etree.Element) -> WangSet:
    """Parse the raw wangset into a pytiled_parser type

    Args:
        raw_wangset: XML Element to be parsed.

    Returns:
        WangSet: A properly typed WangSet.
    """
    colors = []
    for raw_wang_color in raw_wangset.findall("./wangcolor"):
        colors.append(_parse_wang_color(raw_wang_color))

    tiles = {}
    for raw_wang_tile in raw_wangset.findall("./wangtile"):
        tile_id = diagnostics.xml_int_attr(raw_wang_tile, "tileid", field="wangtile")
        tiles[tile_id] = _parse_wang_tile(raw_wang_tile)

    wangset = WangSet(
        name=diagnostics.xml_attr(raw_wangset, "name", field="wangset"),
        tile=diagnostics.xml_int_attr(raw_wangset, "tile", field="wangset"),
        wang_type=diagnostics.xml_attr(raw_wangset, "type", field="wangset"),
        wang_colors=colors,
        wang_tiles=tiles,
    )

    if raw_wangset.attrib.get("class") is not None:
        wangset.class_ = raw_wangset.attrib["class"]

    properties = raw_wangset.find("./properties")
    if properties is not None:
        wangset.properties = parse_properties(properties)

    return wangset
