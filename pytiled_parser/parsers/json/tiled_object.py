"""Object parsing for the JSON Map Format."""

import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from typing_extensions import TypedDict

from pytiled_parser import diagnostics
from pytiled_parser.common_types import OrderedPair, Size
from pytiled_parser.diagnostics import JsonDocument, JsonPath
from pytiled_parser.exception import (
    CircularReferenceError,
    FieldTypeError,
    InvalidFieldValueError,
    MissingFieldError,
    TemplateError,
    UnsupportedTemplateError,
)
from pytiled_parser.parsers.json.properties import RawProperty
from pytiled_parser.parsers.json.properties import parse as parse_properties
from pytiled_parser.tiled_object import (
    Ellipse,
    Point,
    Polygon,
    Polyline,
    Rectangle,
    Text,
    Tile,
    TiledObject,
)
from pytiled_parser.util import load_document, parse_color

RawText = TypedDict(
    "RawText",
    {
        "text": str,
        "color": str,
        "fontfamily": str,
        "pixelsize": float,  # this is `font_size` in Text
        "bold": bool,
        "italic": bool,
        "strikeout": bool,
        "underline": bool,
        "kerning": bool,
        "halign": str,
        "valign": str,
        "wrap": bool,
    },
)
RawText.__doc__ = """
    The keys and their types that appear in a Tiled JSON Text Object.

    Tiled Doc: https://doc.mapeditor.org/en/stable/reference/json-map-format/#text-example
"""


RawObject = TypedDict(
    "RawObject",
    {
        "id": int,
        "gid": int,
        "template": str,
        "x": float,
        "y": float,
        "width": float,
        "height": float,
        "rotation": float,
        "visible": bool,
        "name": str,
        "class": str,
        "type": str,
        "properties": List[RawProperty],
        "ellipse": bool,
        "point": bool,
        "polygon": List[Dict[str, float]],
        "polyline": List[Dict[str, float]],
        "text": RawText,
    },
)
RawObject.__doc__ = """
    The keys and their types that appear in a Tiled JSON Object.

    Tiled Doc: https://doc.mapeditor.org/en/stable/reference/json-map-format/#object
"""

# Coordinates are allowed to be integral-typed in hand-written files.
_NUMBER = (int, float)

# Maximum template nesting depth; guards against very long non-cyclic chains.
_MAX_TEMPLATE_DEPTH = 32


def _require_number(raw_object: Any, key: str, *, path: JsonPath) -> float:
    value = diagnostics.json_require(raw_object, key, _NUMBER, path=path)
    return float(value)


def _parse_common(raw_object: Any, *, path: JsonPath = ()) -> TiledObject:
    """Create an Object containing all the attributes common to all types of objects.

    Args:
        raw_object: Raw object to get common attributes from
        path: Path to this object, relative to the current diagnostic context.

    Returns:
        Object: The attributes in common of all types of objects
    """
    object_id = cast_int(raw_object, "id", path=path)
    x = _require_number(raw_object, "x", path=path)
    y = _require_number(raw_object, "y", path=path)
    visible = diagnostics.json_require(raw_object, "visible", bool, path=path)
    width = _require_number(raw_object, "width", path=path)
    height = _require_number(raw_object, "height", path=path)
    rotation = _require_number(raw_object, "rotation", path=path)
    name = diagnostics.json_require(raw_object, "name", str, path=path)

    common = TiledObject(
        id=object_id,
        coordinates=OrderedPair(x, y),
        visible=visible,
        size=Size(width, height),
        rotation=rotation,
        name=name,
    )

    if raw_object.get("type") is not None:
        common.class_ = cast_str(raw_object, "type", path=path)

    if raw_object.get("class") is not None:
        common.class_ = cast_str(raw_object, "class", path=path)

    if raw_object.get("properties") is not None:
        raw_properties = raw_object["properties"]
        if not isinstance(raw_properties, (list, dict)):
            raise diagnostics.json_error(
                FieldTypeError,
                "Expected an array of properties, got {}".format(
                    type(raw_properties).__name__
                ),
                path=path + ("properties",),
                reason="properties must be an array",
            )
        common.properties = parse_properties(
            raw_properties, path=path + ("properties",)
        )

    return common


def cast_int(raw_object: Any, key: str, *, path: JsonPath = ()) -> int:
    return int(
        diagnostics.json_require(raw_object, key, int, path=path)  # type: ignore[arg-type]
    )


def cast_str(raw_object: Any, key: str, *, path: JsonPath = ()) -> str:
    return str(
        diagnostics.json_require(raw_object, key, str, path=path)  # type: ignore[arg-type]
    )


def _parse_ellipse(raw_object: Any, *, path: JsonPath = ()) -> Ellipse:
    return Ellipse(**_parse_common(raw_object, path=path).__dict__)


def _parse_rectangle(raw_object: Any, *, path: JsonPath = ()) -> Rectangle:
    return Rectangle(**_parse_common(raw_object, path=path).__dict__)


def _parse_point(raw_object: Any, *, path: JsonPath = ()) -> Point:
    return Point(**_parse_common(raw_object, path=path).__dict__)


def _parse_polygon(raw_object: Any, *, path: JsonPath = ()) -> Polygon:
    raw_points = diagnostics.json_require(raw_object, "polygon", list, path=path)
    polygon = []
    for index, point in enumerate(raw_points):
        point_path = path + ("polygon", index)
        if not isinstance(point, dict):
            raise diagnostics.json_error(
                FieldTypeError,
                "Expected an object for polygon point at index {}, got {}".format(
                    index, type(point).__name__
                ),
                path=point_path,
                reason="polygon points are objects",
            )
        polygon.append(
            OrderedPair(
                _require_number(point, "x", path=point_path),
                _require_number(point, "y", path=point_path),
            )
        )

    return Polygon(points=polygon, **_parse_common(raw_object, path=path).__dict__)


def _parse_polyline(raw_object: Any, *, path: JsonPath = ()) -> Polyline:
    raw_points = diagnostics.json_require(raw_object, "polyline", list, path=path)
    polyline = []
    for index, point in enumerate(raw_points):
        point_path = path + ("polyline", index)
        if not isinstance(point, dict):
            raise diagnostics.json_error(
                FieldTypeError,
                "Expected an object for polyline point at index {}, got {}".format(
                    index, type(point).__name__
                ),
                path=point_path,
                reason="polyline points are objects",
            )
        polyline.append(
            OrderedPair(
                _require_number(point, "x", path=point_path),
                _require_number(point, "y", path=point_path),
            )
        )

    return Polyline(points=polyline, **_parse_common(raw_object, path=path).__dict__)


def _parse_tile(
    raw_object: Any,
    new_tileset: Optional[Dict[str, Any]] = None,
    new_tileset_path: Optional[Path] = None,
    *,
    path: JsonPath = (),
) -> Tile:
    gid = cast_int(raw_object, "gid", path=path)

    return Tile(
        gid=gid,
        new_tileset=new_tileset,
        new_tileset_path=new_tileset_path,
        **_parse_common(raw_object, path=path).__dict__,
    )


def _parse_text(raw_object: Any, *, path: JsonPath = ()) -> Text:
    text_path = path + ("text",)
    raw_text = diagnostics.json_require(raw_object, "text", dict, path=path)
    text = diagnostics.json_require(raw_text, "text", str, path=text_path)

    text_object = Text(text=text, **_parse_common(raw_object, path=path).__dict__)

    if raw_text.get("color") is not None:
        color = cast_str(raw_text, "color", path=text_path)
        try:
            text_object.color = parse_color(color)
        except InvalidFieldValueError as error:
            raise diagnostics.json_error(
                InvalidFieldValueError,
                "Invalid text color {!r}".format(color),
                path=text_path + ("color",),
                reason=error.reason or str(error),
            ) from error

    if raw_text.get("fontfamily") is not None:
        text_object.font_family = cast_str(raw_text, "fontfamily", path=text_path)

    if raw_text.get("pixelsize") is not None:
        text_object.font_size = float(
            diagnostics.json_require(raw_text, "pixelsize", _NUMBER, path=text_path)  # type: ignore[arg-type]
        )

    if raw_text.get("bold") is not None:
        text_object.bold = bool(
            diagnostics.json_require(raw_text, "bold", bool, path=text_path)
        )

    if raw_text.get("italic") is not None:
        text_object.italic = bool(
            diagnostics.json_require(raw_text, "italic", bool, path=text_path)
        )

    if raw_text.get("kerning") is not None:
        text_object.kerning = bool(
            diagnostics.json_require(raw_text, "kerning", bool, path=text_path)
        )

    if raw_text.get("strikeout") is not None:
        text_object.strike_out = bool(
            diagnostics.json_require(raw_text, "strikeout", bool, path=text_path)
        )

    if raw_text.get("underline") is not None:
        text_object.underline = bool(
            diagnostics.json_require(raw_text, "underline", bool, path=text_path)
        )

    if raw_text.get("halign") is not None:
        text_object.horizontal_align = cast_str(raw_text, "halign", path=text_path)

    if raw_text.get("valign") is not None:
        text_object.vertical_align = cast_str(raw_text, "valign", path=text_path)

    if raw_text.get("wrap") is not None:
        text_object.wrap = bool(
            diagnostics.json_require(raw_text, "wrap", bool, path=text_path)
        )

    return text_object


def _get_parser(raw_object: RawObject) -> Callable[..., TiledObject]:
    if raw_object.get("ellipse"):
        return _parse_ellipse

    if raw_object.get("point"):
        return _parse_point

    # This is excluded from tests because the coverage is broken. I promise
    # there are tests for Tile objects, but for some reason the coverage
    # isn't picking up this if statement(though it does pickup the _parse_tile)
    # function so who knows
    if raw_object.get("gid"):  # pragma: no cover
        # Only tile objects have the `gid` key
        return _parse_tile

    if raw_object.get("polygon"):
        return _parse_polygon

    if raw_object.get("polyline"):
        return _parse_polyline

    if raw_object.get("text"):
        return _parse_text

    # If it's none of the above, rectangle is the only one left.
    # Rectangle is the only object which has no special properties to signify that.
    return _parse_rectangle


def _diff_display(reference: str, base_dir: Optional[Path]) -> str:
    return diagnostics.display_reference(reference, base_dir)


def _referenced_from(reference_display: str) -> str:
    """Describe where a referenced file was included from.

    The referring document and its current field are taken from the diagnostic
    context *before* entering the referenced document.
    """

    frame = diagnostics.current_frame()
    document = frame.document
    location = document.display_file if document is not None else "<unknown>"
    if frame.field_prefix:
        return "{}: {}".format(location, frame.field_prefix)
    return "{}: template".format(location)


def _validate_template_object(
    template_object: Dict[str, Any],
    *,
    display: str,
) -> None:
    """Validate template fields while the template's own file is in context."""

    number: Any = (int, float)

    for key, expected in (
        ("x", number),
        ("y", number),
        ("width", number),
        ("height", number),
        ("rotation", number),
    ):
        if key in template_object:
            diagnostics.check_type(template_object[key], expected, path=(key,))

    typed_fields = (
        ("id", int),
        ("gid", int),
        ("visible", bool),
        ("name", str),
        ("class", str),
        ("type", str),
        ("ellipse", bool),
        ("point", bool),
        ("template", str),
        ("text", dict),
        ("polygon", list),
        ("polyline", list),
        ("properties", list),
    )
    for key, expected in typed_fields:
        if key in template_object:
            diagnostics.check_type(template_object[key], expected, path=(key,))

    for poly_key in ("polygon", "polyline"):
        raw_points = template_object.get(poly_key)
        if isinstance(raw_points, list):
            for index, point in enumerate(raw_points):
                if not isinstance(point, dict):
                    raise diagnostics.json_error(
                        FieldTypeError,
                        "Expected an object for {} point {} in template '{}'".format(
                            poly_key, index, display
                        ),
                        path=(poly_key, index),
                        reason="points are JSON objects",
                    )
                diagnostics.check_type(
                    point.get("x"), number, path=(poly_key, index, "x")
                )
                diagnostics.check_type(
                    point.get("y"), number, path=(poly_key, index, "y")
                )

    raw_properties = template_object.get("properties")
    if isinstance(raw_properties, list):
        for index, prop in enumerate(raw_properties):
            if not isinstance(prop, dict):
                raise diagnostics.json_error(
                    FieldTypeError,
                    "Expected an object for property {} in template '{}'".format(
                        index, display
                    ),
                    path=("properties", index),
                    reason="properties are JSON objects",
                )
            if "name" not in prop:
                raise diagnostics.json_error(
                    MissingFieldError,
                    "Missing 'name' on property {} in template '{}'".format(
                        index, display
                    ),
                    path=("properties", index, "name"),
                    reason="template properties require a name",
                )
            # Reuse the regular property parser for type/value validation.
            from pytiled_parser.parsers.json.properties import parse_value

            parse_value(
                prop,  # type: ignore[arg-type]
                path=("properties", index),
                name=prop.get("name"),
            )


def _resolve_template(
    reference: str,
    parent_dir: Path,
    *,
    chain: Tuple[str, ...],
    depth: int = 0,
) -> Tuple[Dict[str, Any], Any, Optional[Path], Tuple[str, ...], Tuple[str, ...], Any]:
    """Recursively load and merge a JSON object template chain.

    Returns ``(object, tileset, tileset_path, file_chain, display_chain,
    document)``.  Cycles are detected against *chain* (absolute paths), and
    every error is reported relative to the template file that contains it.
    """

    absolute = os.path.normcase(os.path.abspath(os.fspath(parent_dir / reference)))
    display = _diff_display(reference, parent_dir)

    if absolute in chain:
        cycle = list(chain[chain.index(absolute) :]) + [absolute]
        display_cycle = " -> ".join(_diff_display(item, parent_dir) for item in cycle)
        raise diagnostics.json_error(
            CircularReferenceError,
            "Object templates reference each other in a cycle: {}".format(
                display_cycle
            ),
            path=("template",),
            reason="circular template reference",
        )

    if depth >= _MAX_TEMPLATE_DEPTH:
        raise diagnostics.json_error(
            CircularReferenceError,
            "Object template nesting is deeper than {} levels".format(
                _MAX_TEMPLATE_DEPTH
            ),
            path=("template",),
            reason="template nesting too deep",
        )

    template_path = Path(parent_dir / reference)
    diagnostics.check_external_file(
        template_path,
        reference,
        base_dir=parent_dir,
        json_path=("template",),
    )

    document = load_document(template_path, display=display)
    if not isinstance(document, JsonDocument):
        raise diagnostics.json_error(
            UnsupportedTemplateError,
            "Loading TMX object templates inside a JSON map is currently not "
            "supported, but will be in a future release.",
            path=("template",),
            reason="template format does not match map format",
        )

    data = document.data
    if not isinstance(data, dict):
        raise diagnostics.json_error(
            FieldTypeError,
            "Expected a JSON object at the root of template '{}'".format(display),
            path=(),
            reason="templates must contain an object",
        )

    if data.get("type") != "template":
        raise diagnostics.json_error(
            TemplateError,
            "File '{}' is not a Tiled object template".format(display),
            path=("type",),
            reason='expected "type": "template"',
        )

    template_object = data.get("object")
    if not isinstance(template_object, dict):
        raise diagnostics.json_error(
            MissingFieldError,
            "Object template '{}' has no 'object' entry".format(display),
            path=("object",),
            reason="templates require an 'object' entry",
        )

    new_chain = chain + (absolute,)
    new_display_chain = tuple(_diff_display(item, parent_dir) for item in (reference,))

    # A template's object may itself be based on another template.
    nested_reference = template_object.get("template")
    if nested_reference is not None and not isinstance(nested_reference, str):
        with diagnostics.enter(document=document, json_path=("object",)):
            raise diagnostics.json_error(
                FieldTypeError,
                "Expected a string for 'template' in template '{}', got {}".format(
                    display, type(nested_reference).__name__
                ),
                path=("template",),
                reason="template references are strings",
            )

    with diagnostics.enter(
        document=document,
        json_path=("object",),
        template_chain=diagnostics.current_frame().template_chain + (display,),
        template_files=new_chain,
        parent_dir=parent_dir,
        referenced_from=_referenced_from(display),
    ):
        if nested_reference:
            (
                base_object,
                base_tileset,
                base_tileset_path,
                nested_chain,
                nested_display_chain,
                _base_document,
            ) = _resolve_template(
                nested_reference,
                template_path.parent,
                chain=new_chain,
                depth=depth + 1,
            )
            merged: Dict[str, Any] = dict(base_object)
            _validate_template_object(template_object, display=display)
            _merge_template(
                merged,
                template_object,
                object_path=(),
                template_display=display,
            )
            template_object = merged
            new_chain = nested_chain
            new_display_chain = (display,) + nested_display_chain
            new_tileset = base_tileset
            new_tileset_path = base_tileset_path
        else:
            _validate_template_object(template_object, display=display)
            new_tileset = None
            new_tileset_path = None

    if data.get("tileset") is not None:
        with diagnostics.enter(document=document):
            tileset_info = data["tileset"]
            if not isinstance(tileset_info, dict):
                raise diagnostics.json_error(
                    FieldTypeError,
                    "Expected an object for 'tileset' in template '{}'".format(display),
                    path=("tileset",),
                    reason="tileset references are objects",
                )
            source = cast_str(tileset_info, "source", path=("tileset",))
            tileset_path = Path(template_path.parent / source)
            diagnostics.check_external_file(
                tileset_path,
                source,
                base_dir=template_path.parent,
                json_path=("tileset", "source"),
            )
            tileset_document = load_document(
                tileset_path,
                display=diagnostics.display_reference(source, template_path.parent),
            )
            if isinstance(tileset_document, JsonDocument):
                new_tileset = tileset_document.data
            else:
                new_tileset = tileset_document.root
            new_tileset_path = tileset_path.parent

    return (
        template_object,
        new_tileset,
        new_tileset_path,
        new_chain,
        new_display_chain,
        document,
    )


def _merge_template(
    raw_object: Dict[str, Any],
    template_object: Dict[str, Any],
    *,
    object_path: JsonPath,
    template_display: str,
) -> None:
    """Apply a template's fields onto the raw object, without clobbering overrides."""

    for key, template_value in template_object.items():
        if key == "id":
            # The template's id never applies; the instance has its own.
            continue

        if key == "properties":
            template_properties = template_value
            if not isinstance(template_properties, list):
                raise diagnostics.json_error(
                    FieldTypeError,
                    "Expected an array of properties in template '{}'".format(
                        template_display
                    ),
                    path=("properties",),
                    reason="template properties must be an array",
                )
            if "properties" not in raw_object:
                raw_object["properties"] = []

            existing = raw_object["properties"]
            if not isinstance(existing, list):
                raise diagnostics.json_error(
                    FieldTypeError,
                    "Expected an array of properties on object",
                    path=object_path + ("properties",),
                    reason="properties must be an array",
                )

            existing_names = {
                prop.get("name") for prop in existing if isinstance(prop, dict)
            }
            for prop in template_properties:
                if not isinstance(prop, dict) or "name" not in prop:
                    raise diagnostics.json_error(
                        MissingFieldError,
                        "Missing 'name' on a property in template '{}'".format(
                            template_display
                        ),
                        path=("properties",),
                        reason="template properties require a name",
                    )
                if prop["name"] not in existing_names:
                    existing.append(prop)
                    existing_names.add(prop["name"])
        else:
            raw_object[key] = template_value


def parse(
    raw_object: RawObject,
    parent_dir: Optional[Path] = None,
    *,
    path: JsonPath = (),
    index: Optional[int] = None,
) -> TiledObject:
    """Parse the raw object into a pytiled_parser version

    Args:
        raw_object: Raw object that is to be cast.
        parent_dir: The parent directory that the map file is in.
        path: Path to this object in the JSON document.
        index: Index of this object within its layer, for diagnostics.

    Returns:
        Object: A parsed Object.

    Raises:
        TemplateError: When a template can not be resolved.
        CircularReferenceError: When templates reference each other in a cycle.
    """
    if not isinstance(raw_object, dict):
        raise diagnostics.json_error(
            FieldTypeError,
            "Expected an object, got {}".format(type(raw_object).__name__),
            path=path,
            reason="Tiled objects are JSON objects",
        )

    new_tileset = None
    new_tileset_path = None

    description = diagnostics.describe_object(raw_object, index)
    frame = diagnostics.current_frame()
    with diagnostics.enter(
        json_path=path,
        object=description,
        layer=frame.layer,
    ):
        if raw_object.get("template"):
            if not parent_dir:
                raise diagnostics.json_error(
                    TemplateError,
                    "A parent directory must be specified when using object "
                    "templates.",
                    path=("template",),
                    reason="template resolution needs the map directory",
                )

            template_reference = cast_str(raw_object, "template")
            (
                template_object,
                new_tileset,
                new_tileset_path,
                file_chain,
                display_chain,
                template_document,
            ) = _resolve_template(
                template_reference,
                parent_dir,
                chain=diagnostics.current_frame().template_files,
            )

            with diagnostics.enter(
                json_path=("object",),
                field_prefix="object",
                document=template_document,
                template_chain=diagnostics.current_frame().template_chain
                + display_chain,
                template_files=file_chain,
                parent_dir=parent_dir,
            ):
                _merge_template(
                    raw_object,  # type: ignore[arg-type]
                    template_object,
                    object_path=(),
                    template_display=display_chain[-1],
                )

        if raw_object.get("gid"):
            return _parse_tile(raw_object, new_tileset, new_tileset_path)

        return _get_parser(raw_object)(raw_object)
