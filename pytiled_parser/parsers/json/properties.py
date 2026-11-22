"""Property parsing for the JSON Map Format"""

from pathlib import Path
from typing import List, Optional, Union, cast

from typing_extensions import TypedDict

from pytiled_parser import diagnostics
from pytiled_parser.diagnostics import JsonPath
from pytiled_parser.exception import (
    FieldTypeError,
    InvalidFieldValueError,
    MissingFieldError,
)
from pytiled_parser.properties import Properties, Property
from pytiled_parser.util import parse_color

RawValue = Union[float, str, bool, None]


class RawProperty(TypedDict, total=False):
    """The keys and their values that appear in a Tiled JSON Property Object.

    Tiled Docs: https://doc.mapeditor.org/en/stable/reference/json-map-format/#property
    """

    name: str
    type: str
    value: RawValue


_PROPERTY_TYPES = {
    "string",
    "int",
    "float",
    "bool",
    "color",
    "file",
    "object",
    "class",
}


def parse_value(
    raw_property: RawProperty,
    *,
    path: JsonPath,
    name: Optional[str] = None,
) -> Property:
    """Convert a single raw property into its typed value."""

    if "type" not in raw_property:
        raise diagnostics.json_error(
            MissingFieldError,
            "Missing required field 'type' on property",
            path=path + ("type",),
            reason="property type is required",
        )

    type_ = raw_property["type"]
    if not isinstance(type_, str):
        raise diagnostics.json_error(
            FieldTypeError,
            "Expected a string for property 'type', got {}".format(
                type(type_).__name__
            ),
            path=path + ("type",),
            reason="property types are strings",
        )

    if "value" not in raw_property:
        raise diagnostics.json_error(
            MissingFieldError,
            "Missing required field 'value' on property {!r}".format(
                name if name is not None else raw_property.get("name")
            ),
            path=path + ("value",),
            reason="property value is required",
        )

    raw_value = raw_property["value"]

    if type_ == "file":
        if not isinstance(raw_value, str):
            raise diagnostics.json_error(
                FieldTypeError,
                "Expected a string path for file property {!r}, got {}".format(
                    name, type(raw_value).__name__
                ),
                path=path + ("value",),
                reason="file properties hold a path string",
            )
        return Path(raw_value)

    if type_ == "color":
        if not isinstance(raw_value, str):
            raise diagnostics.json_error(
                FieldTypeError,
                "Expected a color string for property {!r}, got {}".format(
                    name, type(raw_value).__name__
                ),
                path=path + ("value",),
                reason="color properties hold '#AARRGGBB' strings",
            )
        try:
            return cast(Property, parse_color(raw_value))
        except InvalidFieldValueError as error:
            raise diagnostics.json_error(
                InvalidFieldValueError,
                "Invalid color {!r} for property {!r}".format(raw_value, name),
                path=path + ("value",),
                reason=error.reason or str(error),
            ) from error

    if type_ == "int":
        if isinstance(raw_value, bool) or not isinstance(raw_value, int):
            raise diagnostics.json_error(
                FieldTypeError,
                "Expected an integer for property {!r}, got {}: {!r}".format(
                    name, type(raw_value).__name__, raw_value
                ),
                path=path + ("value",),
                reason="int properties hold integers",
            )
        return raw_value

    if type_ == "float":
        if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
            raise diagnostics.json_error(
                FieldTypeError,
                "Expected a number for property {!r}, got {}: {!r}".format(
                    name, type(raw_value).__name__, raw_value
                ),
                path=path + ("value",),
                reason="float properties hold numbers",
            )
        return float(raw_value)

    if type_ == "bool":
        if not isinstance(raw_value, bool):
            raise diagnostics.json_error(
                FieldTypeError,
                "Expected a boolean for property {!r}, got {}: {!r}".format(
                    name, type(raw_value).__name__, raw_value
                ),
                path=path + ("value",),
                reason="bool properties hold true/false",
            )
        return raw_value

    if type_ in ("string", "object", "class"):
        if not isinstance(raw_value, str):
            raise diagnostics.json_error(
                FieldTypeError,
                "Expected a string for property {!r}, got {}: {!r}".format(
                    name, type(raw_value).__name__, raw_value
                ),
                path=path + ("value",),
                reason="{} properties hold strings".format(type_),
            )
        return raw_value

    # Unknown property types are intentionally tolerated.  Tiled allows
    # custom property types in newer versions; passing the raw value through
    # matches the historical behaviour instead of failing the whole map.
    return cast(Property, raw_value)


def parse(
    raw_properties: Union[List[RawProperty], dict],
    *,
    path: JsonPath = (),
) -> Properties:
    """Parse a list of `RawProperty` objects into `Properties`.

    Args:
        raw_properties: The list or dict of `RawProperty` objects to parse.
            The dict type is supported for parsing legacy Tiled dungeon files.

    Returns:
        Properties: The parsed `Property` objects.
    """

    final: Properties = {}

    if isinstance(raw_properties, dict):
        for name, value in raw_properties.items():
            final[name] = cast(Property, value)
        return final

    if not isinstance(raw_properties, list):
        raise diagnostics.json_error(
            FieldTypeError,
            "Expected an array of properties, got {}".format(
                type(raw_properties).__name__
            ),
            path=path,
            reason="properties must be an array",
        )

    for index, raw_property in enumerate(raw_properties):
        property_path = path + (index,)
        if not isinstance(raw_property, dict):
            raise diagnostics.json_error(
                FieldTypeError,
                "Expected an object for property at index {}, got {}".format(
                    index, type(raw_property).__name__
                ),
                path=property_path,
                reason="properties are JSON objects",
            )
        name = raw_property.get("name")
        if not isinstance(name, str):
            raise diagnostics.json_error(
                MissingFieldError if "name" not in raw_property else FieldTypeError,
                (
                    "Missing or invalid 'name' on property at index {}".format(index)
                    if "name" not in raw_property
                    else "Expected a string for property 'name', got {}".format(
                        type(name).__name__
                    )
                ),
                path=property_path + ("name",),
                reason="property name is required",
            )
        final[name] = parse_value(raw_property, path=property_path, name=name)

    return final
