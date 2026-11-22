"""Layer parsing for the TMX Map Format.
"""

import xml.etree.ElementTree as etree
from pathlib import Path
from typing import List, Optional

from pytiled_parser.budget import (
    BudgetTracker,
    convert_raw_tile_layer_data,
    decode_tile_layer_data,
)
from pytiled_parser.common_types import OrderedPair, Size
from pytiled_parser.layer import (
    Chunk,
    ImageLayer,
    Layer,
    LayerGroup,
    ObjectLayer,
    TileLayer,
)
from pytiled_parser.parsers.tmx.properties import parse as parse_properties
from pytiled_parser.parsers.tmx.tiled_object import parse as parse_object
from pytiled_parser.util import parse_color


def _int_attribute(
    raw_element: etree.Element, attribute: str, tracker: BudgetTracker, what: str
) -> int:
    """Read a required integer attribute, with contextual errors.

    Raises MalformedMapError (carrying the file and layer position) when the
    attribute is missing or is not an integer, instead of a bare KeyError or
    ValueError.
    """
    raw_value = raw_element.attrib.get(attribute)
    if raw_value is None:
        tracker.malformed(f"{what} is missing the required '{attribute}' attribute")
    try:
        return int(raw_value)  # type: ignore
    except ValueError:
        tracker.malformed(
            f"{what} has a non-integer '{attribute}' attribute: {raw_value!r}"
        )
    raise AssertionError("unreachable")  # pragma: no cover


def _parse_chunk(
    raw_chunk: etree.Element,
    tracker: BudgetTracker,
    encoding: Optional[str] = None,
    compression: Optional[str] = None,
) -> Chunk:
    """Parse the raw_chunk to a Chunk.

    Args:
        raw_chunk: XML Element to be parsed to a Chunk
        tracker: The budget tracker for the current parse.
        encoding: Encoding type. ("base64" or None)
        compression: Either zlib, gzip, or empty. If empty no decompression is done.

    Returns:
        Chunk: The Chunk created from the raw_chunk
    """
    width = _int_attribute(raw_chunk, "width", tracker, "chunk")
    height = _int_attribute(raw_chunk, "height", tracker, "chunk")

    if encoding == "base64":
        assert isinstance(compression, str)
        data = decode_tile_layer_data(
            tracker, raw_chunk.text, compression, width  # type: ignore
        )
    else:
        data = convert_raw_tile_layer_data(
            tracker,
            [int(v.strip()) for v in raw_chunk.text.split(",")],  # type: ignore
            width,
        )

    return Chunk(
        coordinates=OrderedPair(
            _int_attribute(raw_chunk, "x", tracker, "chunk"),
            _int_attribute(raw_chunk, "y", tracker, "chunk"),
        ),
        size=Size(width, height),
        data=data,
    )


def _parse_common(raw_layer: etree.Element) -> Layer:
    """Create a Layer containing all the attributes common to all layer types.

    This is to create the stub Layer object that can then be used to create the actual
        specific sub-classes of Layer.

    Args:
        raw_layer: XML Element to get common attributes from

    Returns:
        Layer: The attributes in common of all layer types
    """
    if raw_layer.attrib.get("name") is None:
        raw_layer.attrib["name"] = ""

    common = Layer(
        name=raw_layer.attrib["name"],
    )

    if raw_layer.attrib.get("opacity") is not None:
        common.opacity = float(raw_layer.attrib["opacity"])

    if raw_layer.attrib.get("visible") is not None:
        common.visible = bool(int(raw_layer.attrib["visible"]))

    if raw_layer.attrib.get("id") is not None:
        common.id = int(raw_layer.attrib["id"])

    if raw_layer.attrib.get("offsetx") is not None:
        common.offset = OrderedPair(
            float(raw_layer.attrib["offsetx"]), float(raw_layer.attrib["offsety"])
        )

    properties_element = raw_layer.find("./properties")
    if properties_element is not None:
        common.properties = parse_properties(properties_element)

    parallax = [1.0, 1.0]

    if raw_layer.attrib.get("parallaxx") is not None:
        parallax[0] = float(raw_layer.attrib["parallaxx"])

    if raw_layer.attrib.get("parallaxy") is not None:
        parallax[1] = float(raw_layer.attrib["parallaxy"])

    common.parallax_factor = OrderedPair(parallax[0], parallax[1])

    if raw_layer.attrib.get("tintcolor") is not None:
        common.tint_color = parse_color(raw_layer.attrib["tintcolor"])

    if raw_layer.attrib.get("class") is not None:
        common.class_ = raw_layer.attrib["class"]

    if raw_layer.attrib.get("repeatx") is not None:
        common.repeat_x = bool(int(raw_layer.attrib["repeatx"]))

    if raw_layer.attrib.get("repeaty") is not None:
        common.repeat_y = bool(int(raw_layer.attrib["repeaty"]))

    return common


def _parse_tile_layer(raw_layer: etree.Element, tracker: BudgetTracker) -> TileLayer:
    """Parse the raw_layer to a TileLayer.

    Args:
        raw_layer: XML Element to be parsed to a TileLayer.
        tracker: The budget tracker for the current parse.

    Returns:
        TileLayer: The TileLayer created from raw_layer
    """
    with tracker.layer(raw_layer.attrib.get("name")):
        common = _parse_common(raw_layer).__dict__
        del common["size"]

        width = _int_attribute(raw_layer, "width", tracker, "layer")
        height = _int_attribute(raw_layer, "height", tracker, "layer")

        tile_layer = TileLayer(
            size=Size(width, height),
            **common,
        )

        data_element = raw_layer.find("data")
        if data_element is not None:
            encoding = None
            if data_element.attrib.get("encoding") is not None:
                encoding = data_element.attrib["encoding"]

            compression = ""
            if data_element.attrib.get("compression") is not None:
                compression = data_element.attrib["compression"]

            raw_chunks = data_element.findall("chunk")
            if not raw_chunks:
                # A finite tile layer: reject absurd declared dimensions
                # before decoding any data.
                tracker.check_dimensions(width, height, "layer")
                if encoding and encoding != "csv":
                    tile_layer.data = decode_tile_layer_data(
                        tracker=tracker,
                        data=data_element.text,  # type: ignore
                        compression=compression,
                        layer_width=width,
                    )
                else:
                    raw_text = data_element.text or ""
                    # Cheap up-front count so a huge CSV payload is rejected
                    # before materializing hundreds of millions of ints.
                    tracker.check_tile_count(raw_text.count(",") + 1)
                    tile_layer.data = convert_raw_tile_layer_data(
                        tracker,
                        [int(v.strip()) for v in raw_text.split(",")],
                        width,
                    )
            else:
                chunks = []
                total_tiles = 0
                for raw_chunk in raw_chunks:
                    # Reject absurd declared chunk sizes before decoding
                    # anything, and keep the per-layer running total within
                    # budget.
                    total_tiles += tracker.check_dimensions(
                        _int_attribute(raw_chunk, "width", tracker, "chunk"),
                        _int_attribute(raw_chunk, "height", tracker, "chunk"),
                        "chunk",
                    )
                    tracker.check_tile_count(total_tiles)
                    chunks.append(
                        _parse_chunk(
                            raw_chunk,
                            tracker,
                            encoding,
                            compression,
                        )
                    )

                if chunks:
                    tile_layer.chunks = chunks

        return tile_layer


def _parse_object_layer(
    raw_layer: etree.Element,
    encoding: str,
    tracker: BudgetTracker,
    parent_dir: Optional[Path] = None,
) -> ObjectLayer:
    """Parse the raw_layer to an ObjectLayer.

    Args:
        raw_layer: XML Element to be parsed to an ObjectLayer.
        tracker: The budget tracker for the current parse.

    Returns:
        ObjectLayer: The ObjectLayer created from raw_layer
    """
    with tracker.layer(raw_layer.attrib.get("name")):
        raw_objects = raw_layer.findall("./object")
        tracker.add_objects(len(raw_objects))

        objects = []
        for object_ in raw_objects:
            objects.append(parse_object(object_, encoding, parent_dir))

        object_layer = ObjectLayer(
            tiled_objects=objects,
            **_parse_common(raw_layer).__dict__,
        )

        if raw_layer.attrib.get("draworder") is not None:
            object_layer.draw_order = raw_layer.attrib["draworder"]

        return object_layer


def _parse_image_layer(raw_layer: etree.Element, tracker: BudgetTracker) -> ImageLayer:
    """Parse the raw_layer to an ImageLayer.

    Args:
        raw_layer: XML Element to be parsed to an ImageLayer.
        tracker: The budget tracker for the current parse.

    Returns:
        ImageLayer: The ImageLayer created from raw_layer
    """
    with tracker.layer(raw_layer.attrib.get("name")):
        image_element = raw_layer.find("./image")
        if image_element is not None:
            source = Path(image_element.attrib["source"])

            transparent_color = None
            if image_element.attrib.get("trans") is not None:
                transparent_color = parse_color(image_element.attrib["trans"])

            image_layer = ImageLayer(
                image=source,
                transparent_color=transparent_color,
                **_parse_common(raw_layer).__dict__,
            )

            return image_layer

        raise RuntimeError("Tried to parse an image layer that doesn't have an image!")


def _parse_group_layer(
    raw_layer: etree.Element,
    encoding: str,
    tracker: BudgetTracker,
    parent_dir: Optional[Path] = None,
) -> LayerGroup:
    """Parse the raw_layer to a LayerGroup.

    Args:
        raw_layer: XML Element to be parsed to a LayerGroup.
        tracker: The budget tracker for the current parse.

    Returns:
        LayerGroup: The LayerGroup created from raw_layer
    """
    with tracker.layer(raw_layer.attrib.get("name")):
        layers: List[Layer] = []
        for layer in raw_layer.findall("./layer"):
            layers.append(_parse_tile_layer(layer, tracker))

        for layer in raw_layer.findall("./objectgroup"):
            layers.append(_parse_object_layer(layer, encoding, tracker, parent_dir))

        for layer in raw_layer.findall("./imagelayer"):
            layers.append(_parse_image_layer(layer, tracker))

        for layer in raw_layer.findall("./group"):
            layers.append(_parse_group_layer(layer, encoding, tracker, parent_dir))

        return LayerGroup(layers=layers, **_parse_common(raw_layer).__dict__)


def parse(
    raw_layer: etree.Element,
    encoding: str,
    parent_dir: Optional[Path] = None,
    tracker: Optional[BudgetTracker] = None,
) -> Layer:
    """Parse a raw Layer into a pytiled_parser object.

    This function will determine the type of layer and parse accordingly.

    Args:
        raw_layer: Raw layer to be parsed.
        parent_dir: The parent directory that the map file is in.
        tracker: The budget tracker for the current parse. A tracker with
            default limits is created if none is supplied.

    Returns:
        Layer: A parsed Layer.

    Raises:
        RuntimeError: For an invalid layer type being provided
    """
    if tracker is None:
        tracker = BudgetTracker()

    type_ = raw_layer.tag

    if type_ == "objectgroup":
        return _parse_object_layer(raw_layer, encoding, tracker, parent_dir)
    elif type_ == "group":
        return _parse_group_layer(raw_layer, encoding, tracker, parent_dir)
    elif type_ == "imagelayer":
        return _parse_image_layer(raw_layer, tracker)
    elif type_ == "layer":
        return _parse_tile_layer(raw_layer, tracker)
    else:
        raise RuntimeError("Unknown layer type in map file!")
