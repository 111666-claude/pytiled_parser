"""Layer parsing for the JSON Map Format.
"""

from pathlib import Path
from typing import Any, List, Optional, Union, cast

from typing_extensions import TypedDict

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
from pytiled_parser.parsers.json.properties import RawProperty
from pytiled_parser.parsers.json.properties import parse as parse_properties
from pytiled_parser.parsers.json.tiled_object import RawObject
from pytiled_parser.parsers.json.tiled_object import parse as parse_object
from pytiled_parser.util import parse_color

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


def _parse_chunk(
    raw_chunk: RawChunk,
    tracker: BudgetTracker,
    encoding: Optional[str] = None,
    compression: Optional[str] = None,
) -> Chunk:
    """Parse the raw_chunk to a Chunk.

    Args:
        raw_chunk: RawChunk to be parsed to a Chunk
        tracker: The budget tracker for the current parse.
        encoding: Encoding type. ("base64" or None)
        compression: Either zlib, gzip, or empty. If empty no decompression is done.

    Returns:
        Chunk: The Chunk created from the raw_chunk
    """
    if encoding == "base64":
        assert isinstance(compression, str)
        assert isinstance(raw_chunk["data"], str)
        data = decode_tile_layer_data(
            tracker, raw_chunk["data"], compression, raw_chunk.get("width")
        )
    else:
        data = convert_raw_tile_layer_data(
            tracker,
            raw_chunk["data"],  # type: ignore
            raw_chunk.get("width"),
        )

    chunk = Chunk(
        coordinates=OrderedPair(raw_chunk["x"], raw_chunk["y"]),
        size=Size(raw_chunk["width"], raw_chunk["height"]),
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
    common = Layer(
        name=raw_layer["name"],
        opacity=raw_layer["opacity"],
        visible=raw_layer["visible"],
    )

    # if startx is present, starty is present
    if raw_layer.get("startx") is not None:
        common.coordinates = OrderedPair(raw_layer["startx"], raw_layer["starty"])

    if raw_layer.get("id") is not None:
        common.id = raw_layer["id"]

    # if either width or height is present, they both are
    if raw_layer.get("width") is not None:
        common.size = Size(raw_layer["width"], raw_layer["height"])

    if raw_layer.get("offsetx") is not None:
        common.offset = OrderedPair(raw_layer["offsetx"], raw_layer["offsety"])

    if raw_layer.get("properties") is not None:
        common.properties = parse_properties(raw_layer["properties"])

    if raw_layer.get("class") is not None:
        common.class_ = raw_layer["class"]

    parallax = [1.0, 1.0]

    if raw_layer.get("parallaxx") is not None:
        parallax[0] = raw_layer["parallaxx"]

    if raw_layer.get("parallaxy") is not None:
        parallax[1] = raw_layer["parallaxy"]

    common.parallax_factor = OrderedPair(parallax[0], parallax[1])

    if raw_layer.get("tintcolor") is not None:
        common.tint_color = parse_color(raw_layer["tintcolor"])

    if raw_layer.get("repeatx") is not None:
        common.repeat_x = raw_layer["repeatx"]

    if raw_layer.get("repeaty") is not None:
        common.repeat_y = raw_layer["repeaty"]

    return common


def _parse_tile_layer(raw_layer: RawLayer, tracker: BudgetTracker) -> TileLayer:
    """Parse the raw_layer to a TileLayer.

    Args:
        raw_layer: RawLayer to be parsed to a TileLayer.
        tracker: The budget tracker for the current parse.

    Returns:
        TileLayer: The TileLayer created from raw_layer
    """
    with tracker.layer(raw_layer.get("name")):
        tile_layer = TileLayer(**_parse_common(raw_layer).__dict__)

        if raw_layer.get("chunks") is not None:
            tile_layer.chunks = []
            total_tiles = 0
            for chunk in raw_layer["chunks"]:
                # Reject absurd declared chunk sizes before decoding anything,
                # and keep the per-layer running total within budget.
                total_tiles += tracker.check_dimensions(
                    chunk.get("width"), chunk.get("height"), "chunk"
                )
                tracker.check_tile_count(total_tiles)
                if raw_layer.get("encoding") is not None:
                    tile_layer.chunks.append(
                        _parse_chunk(
                            chunk,
                            tracker,
                            raw_layer["encoding"],
                            raw_layer.get("compression"),
                        )
                    )
                else:
                    tile_layer.chunks.append(_parse_chunk(chunk, tracker))

        if raw_layer.get("data") is not None:
            # A finite tile layer must declare its dimensions; the claimed
            # tile count is checked before any data is decoded.
            tracker.check_dimensions(
                raw_layer.get("width"), raw_layer.get("height"), "layer"
            )
            if raw_layer.get("encoding") is not None:
                tile_layer.data = decode_tile_layer_data(
                    tracker=tracker,
                    data=cast(str, raw_layer["data"]),
                    compression=raw_layer.get("compression"),
                    layer_width=raw_layer.get("width"),
                )
            else:
                tile_layer.data = convert_raw_tile_layer_data(
                    tracker,
                    raw_layer["data"],  # type: ignore
                    raw_layer.get("width"),
                )

        return tile_layer


def _parse_object_layer(
    raw_layer: RawLayer,
    encoding: str,
    tracker: BudgetTracker,
    parent_dir: Optional[Path] = None,
) -> ObjectLayer:
    """Parse the raw_layer to an ObjectLayer.

    Args:
        raw_layer: RawLayer to be parsed to an ObjectLayer.
        tracker: The budget tracker for the current parse.

    Returns:
        ObjectLayer: The ObjectLayer created from raw_layer
    """
    with tracker.layer(raw_layer.get("name")):
        tracker.add_objects(len(raw_layer["objects"]))

        objects = []
        for object_ in raw_layer["objects"]:
            objects.append(parse_object(object_, encoding, parent_dir))

        return ObjectLayer(
            tiled_objects=objects,
            draw_order=raw_layer["draworder"],
            **_parse_common(raw_layer).__dict__,
        )


def _parse_image_layer(raw_layer: RawLayer, tracker: BudgetTracker) -> ImageLayer:
    """Parse the raw_layer to an ImageLayer.

    Args:
        raw_layer: RawLayer to be parsed to an ImageLayer.
        tracker: The budget tracker for the current parse.

    Returns:
        ImageLayer: The ImageLayer created from raw_layer
    """
    with tracker.layer(raw_layer.get("name")):
        image_layer = ImageLayer(
            image=Path(raw_layer["image"]), **_parse_common(raw_layer).__dict__
        )

        if raw_layer.get("transparentcolor") is not None:
            image_layer.transparent_color = parse_color(raw_layer["transparentcolor"])

        return image_layer


def _parse_group_layer(
    raw_layer: RawLayer,
    encoding: str,
    tracker: BudgetTracker,
    parent_dir: Optional[Path] = None,
) -> LayerGroup:
    """Parse the raw_layer to a LayerGroup.

    Args:
        raw_layer: RawLayer to be parsed to a LayerGroup.
        tracker: The budget tracker for the current parse.

    Returns:
        LayerGroup: The LayerGroup created from raw_layer
    """
    with tracker.layer(raw_layer.get("name")):
        layers = []

        for layer in raw_layer["layers"]:
            layers.append(parse(layer, encoding, parent_dir=parent_dir, tracker=tracker))

        return LayerGroup(layers=layers, **_parse_common(raw_layer).__dict__)


def parse(
    raw_layer: RawLayer,
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

    type_ = raw_layer["type"]

    if type_ == "objectgroup":
        return _parse_object_layer(raw_layer, encoding, tracker, parent_dir)
    elif type_ == "group":
        return _parse_group_layer(raw_layer, encoding, tracker, parent_dir)
    elif type_ == "imagelayer":
        return _parse_image_layer(raw_layer, tracker)
    elif type_ == "tilelayer":
        return _parse_tile_layer(raw_layer, tracker)

    raise RuntimeError(f"An invalid layer type of {type_} was supplied")
