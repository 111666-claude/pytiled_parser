import xml.etree.ElementTree as etree
from pathlib import Path
from typing import Optional

from pytiled_parser import diagnostics
from pytiled_parser.exception import (
    FieldTypeError,
    InvalidFieldValueError,
    MissingFieldError,
)
from pytiled_parser.properties import Properties, Property
from pytiled_parser.util import parse_color


def _parse_bool(raw_property: etree.Element, value: str) -> bool:
    if value not in ("true", "false"):
        raise diagnostics.xml_error(
            FieldTypeError,
            "Expected 'true' or 'false' for bool property, got {!r}".format(value),
            element=raw_property,
            field="value",
            reason="bool properties must be true or false",
        )
    return value == "true"


def parse(
    raw_properties: etree.Element,
    *,
    field: Optional[str] = "properties",
) -> Properties:
    """Parse an XML ``<properties>`` element.

    Errors reference the property's line/column, its name and the field path.
    """
    final: Properties = {}
    value: Property

    for index, raw_property in enumerate(raw_properties.findall("property")):
        name = raw_property.attrib.get("name")
        property_field = "{}[{}]".format(field or "properties", index)

        if name is None:
            raise diagnostics.xml_error(
                MissingFieldError,
                "Missing required attribute 'name' on <property>",
                element=raw_property,
                field=property_field + ".name",
                reason="properties require a name",
            )

        type_ = raw_property.attrib.get("type")

        if "value" not in raw_property.attrib:
            # Matches historical behaviour: properties without a value are
            # skipped rather than failing the entire map.
            continue

        value_ = raw_property.attrib["value"]
        property_field = "{}[{!r}]".format(field or "properties", name)

        if type_ == "file":
            value = Path(value_)
        elif type_ == "color":
            try:
                value = parse_color(value_)
            except InvalidFieldValueError as error:
                raise diagnostics.xml_error(
                    InvalidFieldValueError,
                    "Invalid color {!r} for property {!r}".format(value_, name),
                    element=raw_property,
                    field=property_field + ".value",
                    reason=error.reason or str(error),
                ) from error
        elif type_ == "int":
            try:
                value = int(value_)
            except (TypeError, ValueError) as error:
                raise diagnostics.xml_error(
                    FieldTypeError,
                    "Expected an integer for property {!r}, got {!r}".format(
                        name, value_
                    ),
                    element=raw_property,
                    field=property_field + ".value",
                    reason=str(error),
                ) from error
        elif type_ == "float":
            try:
                value = float(value_)
            except (TypeError, ValueError) as error:
                raise diagnostics.xml_error(
                    FieldTypeError,
                    "Expected a number for property {!r}, got {!r}".format(
                        name, value_
                    ),
                    element=raw_property,
                    field=property_field + ".value",
                    reason=str(error),
                ) from error
        elif type_ == "bool":
            value = _parse_bool(raw_property, value_)
        else:
            value = value_
        final[name] = value

    return final
