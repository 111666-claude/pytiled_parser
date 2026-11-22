import xml.etree.ElementTree as etree
from pathlib import Path
from typing import Optional

from pytiled_parser import diagnostics
from pytiled_parser.common_types import OrderedPair
from pytiled_parser.exception import (
    InvalidFieldValueError,
)
from pytiled_parser.parsers.tmx.layer import parse as parse_layer
from pytiled_parser.parsers.tmx.properties import parse as parse_properties
from pytiled_parser.parsers.tmx.wang_set import parse as parse_wangset
from pytiled_parser.tileset import Frame, Grid, Tile, Tileset, Transformations
from pytiled_parser.util import parse_color


def _parse_frame(raw_frame: etree.Element) -> Frame:
    return Frame(
        duration=diagnostics.xml_int_attr(raw_frame, "duration", field="frame"),
        tile_id=diagnostics.xml_int_attr(raw_frame, "tileid", field="frame"),
    )


def _parse_grid(raw_grid: etree.Element) -> Grid:
    return Grid(
        orientation=diagnostics.xml_attr(raw_grid, "orientation", field="grid"),
        width=diagnostics.xml_int_attr(raw_grid, "width", field="grid"),
        height=diagnostics.xml_int_attr(raw_grid, "height", field="grid"),
    )


def _parse_transformations(raw_transformations: etree.Element) -> Transformations:
    return Transformations(
        hflip=diagnostics.xml_bool_attr(
            raw_transformations, "hflip", field="transformations"
        ),
        vflip=diagnostics.xml_bool_attr(
            raw_transformations, "vflip", field="transformations"
        ),
        rotate=diagnostics.xml_bool_attr(
            raw_transformations, "rotate", field="transformations"
        ),
        prefer_untransformed=diagnostics.xml_bool_attr(
            raw_transformations,
            "preferuntransformed",
            field="transformations",
        ),
    )


def _parse_tile(raw_tile: etree.Element, external_path: Optional[Path] = None) -> Tile:
    tile = Tile(
        id=diagnostics.xml_int_attr(raw_tile, "id"),  # type: ignore[arg-type]
    )

    if raw_tile.attrib.get("type") is not None:
        tile.class_ = raw_tile.attrib["type"]

    if raw_tile.attrib.get("class") is not None:
        tile.class_ = raw_tile.attrib["class"]

    animation_element = raw_tile.find("./animation")
    if animation_element is not None:
        tile.animation = []
        for raw_frame in animation_element.findall("./frame"):
            tile.animation.append(_parse_frame(raw_frame))

    object_element = raw_tile.find("./objectgroup")
    if object_element is not None:
        tile.objects = parse_layer(object_element)

    properties_element = raw_tile.find("./properties")
    if properties_element is not None:
        tile.properties = parse_properties(properties_element)

    image_element = raw_tile.find("./image")
    if image_element is not None:
        source_attr = diagnostics.xml_attr(
            image_element, "source", field="image.source"
        )
        assert source_attr is not None
        if external_path:
            tile.image = Path(external_path / source_attr).absolute().resolve()
        else:
            tile.image = Path(source_attr)

        tile.image_width = diagnostics.xml_int_attr(
            image_element, "width", field="image"
        )
        tile.width = tile.image_width
        tile.image_height = diagnostics.xml_int_attr(
            image_element, "height", field="image"
        )
        tile.height = tile.image_height

    if raw_tile.attrib.get("x") is not None:
        tile.x = diagnostics.xml_int_attr(raw_tile, "x")  # type: ignore[assignment]

    if raw_tile.attrib.get("y") is not None:
        tile.y = diagnostics.xml_int_attr(raw_tile, "y")  # type: ignore[assignment]

    if raw_tile.attrib.get("width") is not None:
        tile.width = diagnostics.xml_int_attr(  # type: ignore[assignment]
            raw_tile, "width"
        )

    if raw_tile.attrib.get("height") is not None:
        tile.height = diagnostics.xml_int_attr(  # type: ignore[assignment]
            raw_tile, "height"
        )

    return tile


def _parse_tileset(
    raw_tileset: etree.Element,
    firstgid: int,
    external_path: Optional[Path],
) -> Tileset:
    tileset = Tileset(
        name=diagnostics.xml_attr(raw_tileset, "name"),
        tile_count=diagnostics.xml_int_attr(raw_tileset, "tilecount"),
        tile_width=diagnostics.xml_int_attr(raw_tileset, "tilewidth"),
        tile_height=diagnostics.xml_int_attr(raw_tileset, "tileheight"),
        columns=diagnostics.xml_int_attr(raw_tileset, "columns"),
        firstgid=firstgid,
    )

    if raw_tileset.attrib.get("version") is not None:
        tileset.version = raw_tileset.attrib["version"]

    if raw_tileset.attrib.get("tiledversion") is not None:
        tileset.tiled_version = raw_tileset.attrib["tiledversion"]

    if raw_tileset.attrib.get("backgroundcolor") is not None:
        background = raw_tileset.attrib["backgroundcolor"]
        try:
            tileset.background_color = parse_color(background)
        except InvalidFieldValueError as error:
            raise diagnostics.xml_error(
                InvalidFieldValueError,
                "Invalid background color {!r}".format(background),
                element=raw_tileset,
                field="backgroundcolor",
                reason=error.reason or str(error),
            ) from error

    if raw_tileset.attrib.get("spacing") is not None:
        tileset.spacing = diagnostics.xml_int_attr(  # type: ignore[assignment]
            raw_tileset, "spacing"
        )

    if raw_tileset.attrib.get("margin") is not None:
        tileset.margin = diagnostics.xml_int_attr(  # type: ignore[assignment]
            raw_tileset, "margin"
        )

    if raw_tileset.attrib.get("objectalignment") is not None:
        tileset.alignment = raw_tileset.attrib["objectalignment"]

    if raw_tileset.attrib.get("class") is not None:
        tileset.class_ = raw_tileset.attrib["class"]

    if raw_tileset.attrib.get("fillmode") is not None:
        tileset.fill_mode = raw_tileset.attrib["fillmode"]

    if raw_tileset.attrib.get("tilerendersize") is not None:
        tileset.tile_render_size = raw_tileset.attrib["tilerendersize"]

    image_element = raw_tileset.find("image")
    if image_element is not None:
        source_attr = diagnostics.xml_attr(
            image_element, "source", field="image.source"
        )
        assert source_attr is not None
        if external_path:
            tileset.image = Path(external_path / source_attr).absolute().resolve()
        else:
            tileset.image = Path(source_attr)

        tileset.image_width = diagnostics.xml_int_attr(
            image_element, "width", field="image"
        )
        tileset.image_height = diagnostics.xml_int_attr(
            image_element, "height", field="image"
        )

        if image_element.attrib.get("trans") is not None:
            my_string = image_element.attrib["trans"]
            if my_string[0] != "#":
                my_string = f"#{my_string}"
            try:
                tileset.transparent_color = parse_color(my_string)
            except InvalidFieldValueError as error:
                raise diagnostics.xml_error(
                    InvalidFieldValueError,
                    "Invalid transparent color {!r}".format(
                        image_element.attrib["trans"]
                    ),
                    element=image_element,
                    field="image.trans",
                    reason=error.reason or str(error),
                ) from error

    tileoffset_element = raw_tileset.find("./tileoffset")
    if tileoffset_element is not None:
        tileset.tile_offset = OrderedPair(
            diagnostics.xml_int_attr(tileoffset_element, "x", field="tileoffset"),
            diagnostics.xml_int_attr(tileoffset_element, "y", field="tileoffset"),
        )

    grid_element = raw_tileset.find("./grid")
    if grid_element is not None:
        tileset.grid = _parse_grid(grid_element)

    properties_element = raw_tileset.find("./properties")
    if properties_element is not None:
        tileset.properties = parse_properties(properties_element)

    tiles = {}
    for tile_element in raw_tileset.findall("./tile"):
        tile_id = diagnostics.xml_int_attr(tile_element, "id", field="tile")
        tiles[tile_id] = _parse_tile(tile_element, external_path=external_path)
    if tiles:
        tileset.tiles = tiles

    wangsets_element = raw_tileset.find("./wangsets")
    if wangsets_element is not None:
        wangsets = []
        for raw_wangset in wangsets_element.findall("./wangset"):
            wangsets.append(parse_wangset(raw_wangset))
        tileset.wang_sets = wangsets

    transformations_element = raw_tileset.find("./transformations")
    if transformations_element is not None:
        tileset.transformations = _parse_transformations(transformations_element)

    return tileset


def parse(
    raw_tileset: etree.Element,
    firstgid: int,
    external_path: Optional[Path] = None,
) -> Tileset:
    # Tilesets may be embedded in a map, in a template or parsed standalone;
    # errors are labelled by their name when available.
    field = "tileset"

    frame = diagnostics.current_frame()
    with diagnostics.enter(
        field_prefix=field,
        layer=frame.layer,
        object=frame.object,
    ):
        return _parse_tileset(raw_tileset, firstgid, external_path)
