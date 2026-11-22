"""Layer parsing for the TMX Map Format."""

import base64
import gzip
import importlib.util
import xml.etree.ElementTree as etree
import zlib
from pathlib import Path
from typing import List, Optional

from pytiled_parser import diagnostics
from pytiled_parser.common_types import OrderedPair, Size
from pytiled_parser.exception import (
    InvalidFieldValueError,
    MissingFieldError,
    UnknownLayerTypeError,
    UnsupportedCompressionError,
)
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

# This optional zstd include is basically impossible to make a sensible test
# for both ways. It's been tested manually, is unlikely to change or be effected
# so we're just excluding it from test coverage. We are only testing cases where
# zstd is not installed in the test suite, as that is the scenario for 99%
# of use cases most likely.
#
# This does mean that the test suite will fail if zstd is installed, so for
# development purposes it should only be installed when specifically manually
# testing for zstd things.
zstd_spec = importlib.util.find_spec("zstd")
if zstd_spec:  # pragma: no cover
    import zstd
else:
    zstd = None

# Field path used when reporting errors in the layer currently being parsed.
_LAYER_FIELD = "layer"


def _convert_raw_tile_layer_data(data: List[int], layer_width: int) -> List[List[int]]:
    """Convert raw layer data into a nested lit based on the layer width

    Args:
        data: The data to convert
        layer_width: Width of the layer

    Returns:
        List[List[int]]: A nested list containing the converted data
    """
    if layer_width <= 0:
        raise diagnostics.xml_error(
            InvalidFieldValueError,
            "Layer width must be greater than zero, got {}".format(layer_width),
            field="width",
            reason="tile data cannot be split into rows of width {}".format(
                layer_width
            ),
        )

    tile_grid: List[List[int]] = [[]]

    column_count = 0
    row_count = 0
    for item in data:
        column_count += 1
        tile_grid[row_count].append(item)
        if not column_count % layer_width and column_count < len(data):
            row_count += 1
            tile_grid.append([])

    return tile_grid


def _decode_tile_layer_data(
    data: str,
    compression: str,
    layer_width: int,
    *,
    element: Optional[etree.Element] = None,
) -> List[List[int]]:
    """Decode Base64 Encoded tile data. Optionally supports gzip and zlib compression.

    Args:
        data: The base64 encoded data
        compression: Either zlib, gzip, or empty. If empty no decompression is done.
        element: Element the data came from, for diagnostics.

    Returns:
        List[List[int]]: A nested list containing the decoded data

    Raises:
        UnsupportedCompressionError: For an unsupported compression type.
    """
    try:
        # TMX files commonly wrap base64 data across lines; whitespace is not
        # part of the payload.
        compact = "".join(data.split())
        unencoded_data = base64.b64decode(compact, validate=True)
    except (TypeError, ValueError) as error:
        raise diagnostics.xml_error(
            InvalidFieldValueError,
            "Tile data is not valid base64",
            element=element,
            field="data",
            reason=str(error),
        ) from error

    if compression == "zlib":
        try:
            unzipped_data = zlib.decompress(unencoded_data)
        except zlib.error as error:
            raise diagnostics.xml_error(
                InvalidFieldValueError,
                "Could not decompress zlib tile data",
                element=element,
                field="data",
                reason=str(error),
            ) from error
    elif compression == "gzip":
        try:
            unzipped_data = gzip.decompress(unencoded_data)
        except (OSError, EOFError) as error:
            raise diagnostics.xml_error(
                InvalidFieldValueError,
                "Could not decompress gzip tile data",
                element=element,
                field="data",
                reason=str(error),
            ) from error
    elif compression == "zstd" and zstd is None:
        raise diagnostics.xml_error(
            UnsupportedCompressionError,
            "zstd compression support is not installed."
            "To install use 'pip install pytiled-parser[zstd]'",
            element=element,
            field="compression",
            reason="zstd support is not installed",
        )
    # See above note at top of module about zstd tests
    elif compression == "zstd":  # pragma: no cover
        try:
            unzipped_data = zstd.decompress(unencoded_data)
        except Exception as error:
            raise diagnostics.xml_error(
                InvalidFieldValueError,
                "Could not decompress zstd tile data",
                element=element,
                field="data",
                reason=str(error),
            ) from error
    elif compression in ("", None):
        unzipped_data = unencoded_data
    else:
        raise diagnostics.xml_error(
            UnsupportedCompressionError,
            "Unsupported compression {!r}, expected 'zlib', 'gzip', 'zstd' "
            "or none".format(compression),
            element=element,
            field="compression",
            reason="unsupported compression method",
        )

    tile_grid: List[int] = []

    byte_count = 0
    int_count = 0
    int_value = 0
    for byte in unzipped_data:
        int_value += byte << (byte_count * 8)
        byte_count += 1
        if not byte_count % 4:
            byte_count = 0
            int_count += 1
            tile_grid.append(int_value)
            int_value = 0

    return _convert_raw_tile_layer_data(tile_grid, layer_width)


def _parse_chunk(
    raw_chunk: etree.Element,
    encoding: Optional[str] = None,
    compression: Optional[str] = None,
) -> Chunk:
    """Parse the raw_chunk to a Chunk.

    Args:
        raw_chunk: XML Element to be parsed to a Chunk
        encoding: Encoding type. ("base64" or None)
        compression: Either zlib, gzip, or empty. If empty no decompression is done.

    Returns:
        Chunk: The Chunk created from the raw_chunk
    """
    width = diagnostics.xml_int_attr(raw_chunk, "width", field="chunk")
    height = diagnostics.xml_int_attr(raw_chunk, "height", field="chunk")
    x = diagnostics.xml_int_attr(raw_chunk, "x", field="chunk")
    y = diagnostics.xml_int_attr(raw_chunk, "y", field="chunk")

    if encoding == "base64":
        assert isinstance(compression, str)
        text = diagnostics.xml_text(raw_chunk, field="chunk.data")
        assert text is not None
        data = _decode_tile_layer_data(
            text, compression, width, element=raw_chunk  # type: ignore[arg-type]
        )
    else:
        data = _convert_raw_tile_layer_data(
            diagnostics.xml_int_list(raw_chunk, field="chunk.data"),
            width,  # type: ignore[arg-type]
        )

    return Chunk(
        coordinates=OrderedPair(x, y),  # type: ignore[arg-type]
        size=Size(width, height),  # type: ignore[arg-type]
        data=data,
    )


def _parse_common(raw_layer: etree.Element) -> Layer:
    """Create a Layer containing all the attributes common to all layer types.

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
        common.opacity = diagnostics.xml_float_attr(  # type: ignore[assignment]
            raw_layer, "opacity"
        )

    if raw_layer.attrib.get("visible") is not None:
        common.visible = diagnostics.xml_bool_attr(  # type: ignore[assignment]
            raw_layer, "visible"
        )

    if raw_layer.attrib.get("id") is not None:
        common.id = diagnostics.xml_int_attr(raw_layer, "id")  # type: ignore[assignment]

    if raw_layer.attrib.get("offsetx") is not None:
        common.offset = OrderedPair(
            diagnostics.xml_float_attr(raw_layer, "offsetx"),
            diagnostics.xml_float_attr(raw_layer, "offsety"),  # type: ignore[arg-type]
        )

    properties_element = raw_layer.find("./properties")
    if properties_element is not None:
        common.properties = parse_properties(properties_element)

    parallax = [1.0, 1.0]

    if raw_layer.attrib.get("parallaxx") is not None:
        parallax[0] = diagnostics.xml_float_attr(  # type: ignore[assignment]
            raw_layer, "parallaxx"
        )

    if raw_layer.attrib.get("parallaxy") is not None:
        parallax[1] = diagnostics.xml_float_attr(  # type: ignore[assignment]
            raw_layer, "parallaxy"
        )

    common.parallax_factor = OrderedPair(parallax[0], parallax[1])

    if raw_layer.attrib.get("tintcolor") is not None:
        tint = raw_layer.attrib["tintcolor"]
        try:
            common.tint_color = parse_color(tint)
        except InvalidFieldValueError as error:
            raise diagnostics.xml_error(
                InvalidFieldValueError,
                "Invalid tint color {!r}".format(tint),
                element=raw_layer,
                field="tintcolor",
                reason=error.reason or str(error),
            ) from error

    if raw_layer.attrib.get("class") is not None:
        common.class_ = raw_layer.attrib["class"]

    if raw_layer.attrib.get("repeatx") is not None:
        common.repeat_x = diagnostics.xml_bool_attr(  # type: ignore[assignment]
            raw_layer, "repeatx"
        )

    if raw_layer.attrib.get("repeaty") is not None:
        common.repeat_y = diagnostics.xml_bool_attr(  # type: ignore[assignment]
            raw_layer, "repeaty"
        )

    return common


def _parse_tile_layer(raw_layer: etree.Element) -> TileLayer:
    """Parse the raw_layer to a TileLayer.

    Args:
        raw_layer: XML Element to be parsed to a TileLayer.

    Returns:
        TileLayer: The TileLayer created from raw_layer
    """
    common = _parse_common(raw_layer).__dict__
    del common["size"]

    width = diagnostics.xml_int_attr(raw_layer, "width")
    height = diagnostics.xml_int_attr(raw_layer, "height")

    tile_layer = TileLayer(
        size=Size(width, height),  # type: ignore[arg-type]
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
            if encoding and encoding != "csv":
                text = diagnostics.xml_text(data_element, field="data")
                assert text is not None
                if encoding not in ("base64",):
                    raise diagnostics.xml_error(
                        UnsupportedCompressionError,
                        "Unsupported tile encoding {!r}, expected 'base64' or "
                        "'csv'".format(encoding),
                        element=data_element,
                        field="encoding",
                        reason="unsupported encoding",
                    )
                tile_layer.data = _decode_tile_layer_data(
                    data=text,
                    compression=compression,
                    layer_width=width,  # type: ignore[arg-type]
                    element=data_element,
                )
            else:
                tile_layer.data = _convert_raw_tile_layer_data(
                    diagnostics.xml_int_list(data_element, field="data"),
                    width,  # type: ignore[arg-type]
                )
        else:
            chunks = []
            for raw_chunk in raw_chunks:
                chunks.append(
                    _parse_chunk(
                        raw_chunk,
                        encoding,
                        compression,
                    )
                )

            if chunks:
                tile_layer.chunks = chunks

    return tile_layer


def _parse_object_layer(
    raw_layer: etree.Element, parent_dir: Optional[Path] = None
) -> ObjectLayer:
    """Parse the raw_layer to an ObjectLayer.

    Args:
        raw_layer: XML Element to be parsed to an ObjectLayer.

    Returns:
        ObjectLayer: The ObjectLayer created from raw_layer
    """
    objects = []
    for index, object_ in enumerate(raw_layer.findall("./object")):
        objects.append(parse_object(object_, parent_dir, index=index))

    object_layer = ObjectLayer(
        tiled_objects=objects,
        **_parse_common(raw_layer).__dict__,
    )

    if raw_layer.attrib.get("draworder") is not None:
        object_layer.draw_order = raw_layer.attrib["draworder"]

    return object_layer


def _parse_image_layer(raw_layer: etree.Element) -> ImageLayer:
    """Parse the raw_layer to an ImageLayer.

    Args:
        raw_layer: XML Element to be parsed to an ImageLayer.

    Returns:
        ImageLayer: The ImageLayer created from raw_layer
    """
    image_element = raw_layer.find("./image")
    if image_element is not None:
        source_attr = diagnostics.xml_attr(
            image_element, "source", field="image.source"
        )
        assert source_attr is not None
        source = Path(source_attr)

        transparent_color = None
        if image_element.attrib.get("trans") is not None:
            transparent = image_element.attrib["trans"]
            try:
                transparent_color = parse_color(transparent)
            except InvalidFieldValueError as error:
                raise diagnostics.xml_error(
                    InvalidFieldValueError,
                    "Invalid transparent color {!r}".format(transparent),
                    element=image_element,
                    field="image.trans",
                    reason=error.reason or str(error),
                ) from error

        image_layer = ImageLayer(
            image=source,
            transparent_color=transparent_color,
            **_parse_common(raw_layer).__dict__,
        )

        return image_layer

    raise diagnostics.xml_error(
        MissingFieldError,
        "Image layer has no <image> child",
        element=raw_layer,
        field="image",
        reason="image layers require an <image> element",
    )


def _parse_group_layer(
    raw_layer: etree.Element, parent_dir: Optional[Path] = None
) -> LayerGroup:
    """Parse the raw_layer to a LayerGroup.

    Args:
        raw_layer: XML Element to be parsed to a LayerGroup.

    Returns:
        LayerGroup: The LayerGroup created from raw_layer
    """
    layers: List[Layer] = []
    index = 0
    for layer in raw_layer.findall("./layer"):
        layers.append(parse(layer, parent_dir, index=index))
        index += 1

    for layer in raw_layer.findall("./objectgroup"):
        layers.append(parse(layer, parent_dir, index=index))
        index += 1

    for layer in raw_layer.findall("./imagelayer"):
        layers.append(parse(layer, parent_dir, index=index))
        index += 1

    for layer in raw_layer.findall("./group"):
        layers.append(parse(layer, parent_dir, index=index))
        index += 1

    return LayerGroup(layers=layers, **_parse_common(raw_layer).__dict__)


def parse(
    raw_layer: etree.Element,
    parent_dir: Optional[Path] = None,
    *,
    index: Optional[int] = None,
) -> Layer:
    """Parse a raw Layer into a pytiled_parser object.

    This function will determine the type of layer and parse accordingly.

    Args:
        raw_layer: Raw layer to be parsed.
        parent_dir: The parent directory that the map file is in.
        index: Index of this layer within its parent, used for diagnostics.

    Returns:
        Layer: A parsed Layer.

    Raises:
        UnknownLayerTypeError: For an invalid layer type being provided
    """
    type_ = raw_layer.tag

    description = diagnostics.describe_xml_layer(raw_layer)
    if description.startswith("<") and index is not None:
        description = "<{} index {}>".format(type_, index)

    parent_layer = diagnostics.current_frame().layer
    # Nested layers already live under a "layer" prefix; do not repeat it.
    already_in_layer = diagnostics.current_frame().field_prefix.startswith(_LAYER_FIELD)
    with diagnostics.enter(
        layer=diagnostics.chain_layer(parent_layer, description),
        field_prefix=None if already_in_layer else _LAYER_FIELD,
        parent_dir=(
            parent_dir
            if parent_dir is not None
            else diagnostics.current_frame().parent_dir
        ),
    ):
        if type_ == "objectgroup":
            return _parse_object_layer(raw_layer, parent_dir)
        elif type_ == "group":
            return _parse_group_layer(raw_layer, parent_dir)
        elif type_ == "imagelayer":
            return _parse_image_layer(raw_layer)
        elif type_ == "layer":
            return _parse_tile_layer(raw_layer)
        else:
            raise diagnostics.xml_error(
                UnknownLayerTypeError,
                "Unknown layer type <{}>. Expected one of <layer>, "
                "<objectgroup>, <imagelayer> or <group>.".format(type_),
                element=raw_layer,
                field="type",
                reason="unrecognized layer element",
            )
