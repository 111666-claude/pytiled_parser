"""Layer parsing for the JSON Map Format."""

import base64
import gzip
import importlib.util
import zlib
from pathlib import Path
from typing import Any, List, Optional, Union, cast

from typing_extensions import TypedDict

from pytiled_parser import diagnostics
from pytiled_parser.common_types import OrderedPair, Size
from pytiled_parser.diagnostics import JsonPath
from pytiled_parser.exception import (
    FieldTypeError,
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
from pytiled_parser.parsers.json.properties import RawProperty
from pytiled_parser.parsers.json.properties import parse as parse_properties
from pytiled_parser.parsers.json.tiled_object import RawObject
from pytiled_parser.parsers.json.tiled_object import parse as parse_object
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


RawChunk = TypedDict(
    "RawChunk",
    {"data": Union[List[int], str], "height": int, "width": int, "x": int, "y": int},
)
RawChunk.__doc__ = """
    The keys and their types that appear in a Tiled JSON Chunk Object.

    Tiled Doc: https://doc.mapeditor.org/en/stable/reference/json-map-format/#chunk
"""


RawLayer = TypedDict(
    "RawLayer",
    {
        "chunks": List[RawChunk],
        "compression": str,
        "data": Union[List[int], str],
        "draworder": str,
        "encoding": str,
        "height": int,
        "id": int,
        "image": str,
        "layers": List[Any],
        "name": str,
        "objects": List[RawObject],
        "offsetx": float,
        "offsety": float,
        "parallaxx": float,
        "parallaxy": float,
        "opacity": float,
        "properties": List[RawProperty],
        "startx": int,
        "starty": int,
        "tintcolor": str,
        "transparentcolor": str,
        "class": str,
        "type": str,
        "visible": bool,
        "width": int,
        "x": int,
        "y": int,
        "repeatx": bool,
        "repeaty": bool,
    },
)
RawLayer.__doc__ = """
    The keys and their types that appear in a Tiled JSON Layer Object.

    Tiled Doc: https://doc.mapeditor.org/en/stable/reference/json-map-format/#layer
"""


def _convert_raw_tile_layer_data(data: List[int], layer_width: int) -> List[List[int]]:
    """Convert raw layer data into a nested lit based on the layer width

    Args:
        data: The data to convert
        layer_width: Width of the layer

    Returns:
        List[List[int]]: A nested list containing the converted data
    """
    tile_grid: List[List[int]] = [[]]

    if layer_width <= 0:
        raise diagnostics.json_error(
            InvalidFieldValueError,
            "Layer width must be greater than zero, got {}".format(layer_width),
            path=("width",),
            reason="tile data cannot be split into rows of width {}".format(
                layer_width
            ),
        )

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
    data: str, compression: str, layer_width: int
) -> List[List[int]]:
    """Decode Base64 Encoded tile data. Optionally supports gzip and zlib compression.

    Args:
        data: The base64 encoded data
        compression: Either zlib, gzip, or empty. If empty no decompression is done.

    Returns:
        List[List[int]]: A nested list containing the decoded data

    Raises:
        UnsupportedCompressionError: For an unsupported compression type.
    """
    try:
        compact = "".join(data.split())
        unencoded_data = base64.b64decode(compact, validate=True)
    except (ValueError, TypeError) as error:
        raise diagnostics.json_error(
            InvalidFieldValueError,
            "Tile data is not valid base64",
            path=("data",),
            reason=str(error),
        ) from error

    if compression == "zlib":
        try:
            unzipped_data = zlib.decompress(unencoded_data)
        except zlib.error as error:
            raise diagnostics.json_error(
                InvalidFieldValueError,
                "Could not decompress zlib tile data",
                path=("data",),
                reason=str(error),
            ) from error
    elif compression == "gzip":
        try:
            unzipped_data = gzip.decompress(unencoded_data)
        except (OSError, EOFError) as error:
            raise diagnostics.json_error(
                InvalidFieldValueError,
                "Could not decompress gzip tile data",
                path=("data",),
                reason=str(error),
            ) from error
    elif compression == "zstd" and zstd is None:
        raise diagnostics.json_error(
            UnsupportedCompressionError,
            "zstd compression support is not installed."
            "To install use 'pip install pytiled-parser[zstd]'",
            path=("compression",),
            reason="zstd support is not installed",
        )
    # See above note at top of module about zstd tests
    elif compression == "zstd":  # pragma: no cover
        try:
            unzipped_data = zstd.decompress(unencoded_data)
        except Exception as error:
            raise diagnostics.json_error(
                InvalidFieldValueError,
                "Could not decompress zstd tile data",
                path=("data",),
                reason=str(error),
            ) from error
    elif compression in ("", None):
        unzipped_data = unencoded_data
    else:
        raise diagnostics.json_error(
            UnsupportedCompressionError,
            "Unsupported compression {!r}, expected 'zlib', 'gzip', 'zstd' "
            "or none".format(compression),
            path=("compression",),
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


def _require_layer_width(raw_layer: RawLayer, path: JsonPath) -> int:
    return cast(
        int,
        diagnostics.json_require(raw_layer, "width", int, path=path),
    )


def _parse_chunk(
    raw_chunk: RawChunk,
    encoding: Optional[str] = None,
    compression: Optional[str] = None,
    *,
    path: JsonPath = (),
) -> Chunk:
    """Parse the raw_chunk to a Chunk.

    Args:
        raw_chunk: RawChunk to be parsed to a Chunk
        encoding: Encoding type. ("base64" or None)
        compression: Either zlib, gzip, or empty. If empty no decompression is done.

    Returns:
        Chunk: The Chunk created from the raw_chunk
    """
    if not isinstance(raw_chunk, dict):
        raise diagnostics.json_error(
            FieldTypeError,
            "Expected an object for chunk, got {}".format(type(raw_chunk).__name__),
            path=path,
            reason="chunks are JSON objects",
        )

    chunk_path = path
    width = cast(
        int, diagnostics.json_require(raw_chunk, "width", int, path=chunk_path)
    )
    height = cast(
        int, diagnostics.json_require(raw_chunk, "height", int, path=chunk_path)
    )
    x = cast(int, diagnostics.json_require(raw_chunk, "x", int, path=chunk_path))
    y = cast(int, diagnostics.json_require(raw_chunk, "y", int, path=chunk_path))

    if encoding == "base64":
        data_ = diagnostics.json_require(raw_chunk, "data", str, path=chunk_path)
        assert isinstance(compression, str)
        data = _decode_tile_layer_data(data_, compression, width)
    else:
        raw_data = diagnostics.json_require(raw_chunk, "data", list, path=chunk_path)
        for index, item in enumerate(cast(List[Any], raw_data)):
            if isinstance(item, bool) or not isinstance(item, int):
                raise diagnostics.json_error(
                    FieldTypeError,
                    "Expected integers in chunk data, got {} at index {}".format(
                        type(item).__name__, index
                    ),
                    path=chunk_path + ("data", index),
                    reason="tile ids must be integers",
                )
        data = _convert_raw_tile_layer_data(cast(List[int], raw_data), width)

    chunk = Chunk(
        coordinates=OrderedPair(x, y),
        size=Size(width, height),
        data=data,
    )

    return chunk


def _parse_common(raw_layer: RawLayer) -> Layer:
    """Create a Layer containing all the attributes common to all layer types.

    This is to create the stub Layer object that can then be used to create the actual
        specific sub-classes of Layer.

    Args:
        raw_layer: Raw layer get common attributes from

    Returns:
        Layer: The attributes in common of all layer types
    """
    name = cast(str, diagnostics.json_require(raw_layer, "name", str))
    opacity = cast(float, diagnostics.json_require(raw_layer, "opacity", float))
    visible = cast(bool, diagnostics.json_require(raw_layer, "visible", bool))

    common = Layer(
        name=name,
        opacity=opacity,
        visible=visible,
    )

    # if startx is present, starty is present
    if raw_layer.get("startx") is not None:
        startx = cast(int, diagnostics.json_optional(raw_layer, "startx", int))
        starty = cast(int, diagnostics.json_require(raw_layer, "starty", int))
        common.coordinates = OrderedPair(startx, starty)
    elif raw_layer.get("starty") is not None:  # pragma: no cover - defensive
        diagnostics.json_require(raw_layer, "starty", int)

    if raw_layer.get("id") is not None:
        common.id = cast(int, diagnostics.json_optional(raw_layer, "id", int))

    # if either width or height is present, they both are
    if raw_layer.get("width") is not None:
        width = cast(int, diagnostics.json_optional(raw_layer, "width", int))
        height = cast(int, diagnostics.json_require(raw_layer, "height", int))
        common.size = Size(width, height)
    elif raw_layer.get("height") is not None:  # pragma: no cover - defensive
        diagnostics.json_require(raw_layer, "height", int)

    if raw_layer.get("offsetx") is not None:
        offsetx = cast(float, diagnostics.json_optional(raw_layer, "offsetx", float))
        offsety = cast(float, diagnostics.json_require(raw_layer, "offsety", float))
        common.offset = OrderedPair(offsetx, offsety)

    if raw_layer.get("properties") is not None:
        common.properties = parse_properties(
            raw_layer["properties"],
            path=("properties",),
        )

    if raw_layer.get("class") is not None:
        common.class_ = cast(str, diagnostics.json_optional(raw_layer, "class", str))

    parallax = [1.0, 1.0]

    if raw_layer.get("parallaxx") is not None:
        parallax[0] = cast(
            float, diagnostics.json_optional(raw_layer, "parallaxx", float)
        )

    if raw_layer.get("parallaxy") is not None:
        parallax[1] = cast(
            float, diagnostics.json_optional(raw_layer, "parallaxy", float)
        )

    common.parallax_factor = OrderedPair(parallax[0], parallax[1])

    if raw_layer.get("tintcolor") is not None:
        tint = cast(str, diagnostics.json_optional(raw_layer, "tintcolor", str))
        try:
            common.tint_color = parse_color(tint)
        except InvalidFieldValueError as error:
            raise diagnostics.json_error(
                InvalidFieldValueError,
                "Invalid tint color {!r}".format(tint),
                path=("tintcolor",),
                reason=error.reason or str(error),
            ) from error

    if raw_layer.get("repeatx") is not None:
        common.repeat_x = cast(
            bool, diagnostics.json_optional(raw_layer, "repeatx", bool)
        )

    if raw_layer.get("repeaty") is not None:
        common.repeat_y = cast(
            bool, diagnostics.json_optional(raw_layer, "repeaty", bool)
        )

    return common


def _parse_tile_layer(raw_layer: RawLayer) -> TileLayer:
    """Parse the raw_layer to a TileLayer.

    Args:
        raw_layer: RawLayer to be parsed to a TileLayer.

    Returns:
        TileLayer: The TileLayer created from raw_layer
    """
    tile_layer = TileLayer(**_parse_common(raw_layer).__dict__)

    if raw_layer.get("chunks") is not None:
        raw_chunks = diagnostics.json_require(raw_layer, "chunks", list)
        tile_layer.chunks = []
        for index, chunk in enumerate(cast(List[RawChunk], raw_chunks)):
            if raw_layer.get("encoding") is not None:
                encoding = cast(
                    str, diagnostics.json_optional(raw_layer, "encoding", str)
                )
                compression = cast(
                    str,
                    diagnostics.json_optional(
                        raw_layer, "compression", str, default=""
                    ),
                )
                tile_layer.chunks.append(
                    _parse_chunk(
                        chunk,
                        encoding,
                        compression,
                        path=("chunks", index),
                    )
                )
            else:
                tile_layer.chunks.append(_parse_chunk(chunk, path=("chunks", index)))

    if raw_layer.get("data") is not None:
        if raw_layer.get("encoding") is not None:
            encoding = cast(str, diagnostics.json_optional(raw_layer, "encoding", str))
            compression = cast(
                str,
                diagnostics.json_optional(raw_layer, "compression", str, default=""),
            )
            width = _require_layer_width(raw_layer, ("width",))
            if not isinstance(raw_layer["data"], str):
                raise diagnostics.json_error(
                    FieldTypeError,
                    "Expected base64 encoded string data for layer with encoding "
                    "{!r}, got {}".format(encoding, type(raw_layer["data"]).__name__),
                    path=("data",),
                    reason="encoded tile data must be a string",
                )
            if encoding not in ("base64", "csv"):
                raise diagnostics.json_error(
                    UnsupportedCompressionError,
                    "Unsupported tile encoding {!r}, expected 'base64' or 'csv'".format(
                        encoding
                    ),
                    path=("encoding",),
                    reason="unsupported encoding",
                )
            if encoding == "base64":
                tile_layer.data = _decode_tile_layer_data(
                    data=raw_layer["data"],
                    compression=compression,
                    layer_width=width,
                )
            else:  # pragma: no cover - csv with the JSON format is unusual
                values = []
                for index, item in enumerate(raw_layer["data"].split(",")):
                    try:
                        values.append(int(item.strip()))
                    except ValueError as error:
                        raise diagnostics.json_error(
                            InvalidFieldValueError,
                            "Invalid integer {!r} at index {} in csv layer data".format(
                                item.strip(), index
                            ),
                            path=("data",),
                            reason=str(error),
                        ) from error
                tile_layer.data = _convert_raw_tile_layer_data(values, width)
        else:
            width = _require_layer_width(raw_layer, ("width",))
            raw_data = diagnostics.json_require(raw_layer, "data", list)
            for index, item in enumerate(cast(List[Any], raw_data)):
                if isinstance(item, bool) or not isinstance(item, int):
                    raise diagnostics.json_error(
                        FieldTypeError,
                        "Expected integers in layer data, got {} at index {}".format(
                            type(item).__name__, index
                        ),
                        path=("data", index),
                        reason="tile ids must be integers",
                    )
            tile_layer.data = _convert_raw_tile_layer_data(
                cast(List[int], raw_data), width
            )

    return tile_layer


def _parse_object_layer(
    raw_layer: RawLayer,
    parent_dir: Optional[Path] = None,
) -> ObjectLayer:
    """Parse the raw_layer to an ObjectLayer.

    Args:
        raw_layer: RawLayer to be parsed to an ObjectLayer.

    Returns:
        ObjectLayer: The ObjectLayer created from raw_layer
    """
    raw_objects = diagnostics.json_require(raw_layer, "objects", list)
    draworder = cast(str, diagnostics.json_require(raw_layer, "draworder", str))

    objects = []
    for index, object_ in enumerate(cast(List[RawObject], raw_objects)):
        objects.append(
            parse_object(object_, parent_dir, path=("objects", index), index=index)
        )

    return ObjectLayer(
        tiled_objects=objects,
        draw_order=draworder,
        **_parse_common(raw_layer).__dict__,
    )


def _parse_image_layer(raw_layer: RawLayer) -> ImageLayer:
    """Parse the raw_layer to an ImageLayer.

    Args:
        raw_layer: RawLayer to be parsed to an ImageLayer.

    Returns:
        ImageLayer: The ImageLayer created from raw_layer
    """
    image = cast(str, diagnostics.json_require(raw_layer, "image", str))
    image_layer = ImageLayer(image=Path(image), **_parse_common(raw_layer).__dict__)

    if raw_layer.get("transparentcolor") is not None:
        transparent = cast(
            str, diagnostics.json_optional(raw_layer, "transparentcolor", str)
        )
        try:
            image_layer.transparent_color = parse_color(transparent)
        except InvalidFieldValueError as error:
            raise diagnostics.json_error(
                InvalidFieldValueError,
                "Invalid transparent color {!r}".format(transparent),
                path=("transparentcolor",),
                reason=error.reason or str(error),
            ) from error

    return image_layer


def _parse_group_layer(
    raw_layer: RawLayer, parent_dir: Optional[Path] = None
) -> LayerGroup:
    """Parse the raw_layer to a LayerGroup.

    Args:
        raw_layer: RawLayer to be parsed to a LayerGroup.

    Returns:
        LayerGroup: The LayerGroup created from raw_layer
    """
    raw_layers = diagnostics.json_require(raw_layer, "layers", list)

    layers = []

    for index, layer in enumerate(cast(List[RawLayer], raw_layers)):
        layers.append(
            parse(
                layer,
                parent_dir=parent_dir,
                path=("layers", index),
                index=index,
            )
        )

    return LayerGroup(layers=layers, **_parse_common(raw_layer).__dict__)


def parse(
    raw_layer: RawLayer,
    parent_dir: Optional[Path] = None,
    *,
    path: JsonPath = (),
    index: Optional[int] = None,
) -> Layer:
    """Parse a raw Layer into a pytiled_parser object.

    This function will determine the type of layer and parse accordingly.

    Args:
        raw_layer: Raw layer to be parsed.
        parent_dir: The parent directory that the map file is in.
        path: Path to this layer within the JSON document.
        index: Index of this layer within its parent, used for diagnostics.

    Returns:
        Layer: A parsed Layer.

    Raises:
        UnknownLayerTypeError: For an invalid layer type being provided
    """
    if not isinstance(raw_layer, dict):
        raise diagnostics.json_error(
            FieldTypeError,
            "Expected a layer object, got {}".format(type(raw_layer).__name__),
            path=path,
            reason="layers are JSON objects",
        )

    type_ = raw_layer.get("type")
    parent_layer = diagnostics.current_frame().layer
    if not isinstance(type_, str):
        raise diagnostics.json_error(
            MissingFieldError,
            "Missing required field 'type' on layer {}".format(
                diagnostics.describe_layer(raw_layer, index)
            ),
            path=path + ("type",),
            reason="layer type is required",
        )

    description = diagnostics.describe_layer(raw_layer, index)
    with diagnostics.enter(
        json_path=path,
        layer=diagnostics.chain_layer(parent_layer, description),
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
        elif type_ == "tilelayer":
            return _parse_tile_layer(raw_layer)

        raise diagnostics.json_error(
            UnknownLayerTypeError,
            "Unknown layer type {!r} for layer {}. Expected one of "
            "'tilelayer', 'objectgroup', 'imagelayer' or 'group'.".format(
                type_, description
            ),
            path=("type",),
            reason="unrecognized layer type",
        )
