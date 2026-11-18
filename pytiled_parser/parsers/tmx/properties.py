import xml.etree.ElementTree as etree
from pathlib import Path

from pytiled_parser.properties import ObjectRef, Properties, Property
from pytiled_parser.util import parse_color


def _parse_property_value(raw_property: etree.Element, value_: str) -> Property:
    """Parse a single scalar ``<property>`` value based on its ``type``."""
    type_ = raw_property.attrib.get("type")

    if type_ == "file":
        return Path(value_)
    elif type_ == "color":
        return parse_color(value_)
    elif type_ == "int":
        return round(float(value_))
    elif type_ == "float":
        return float(value_)
    elif type_ == "bool":
        return value_ == "true"
    elif type_ == "object":
        # Object references store the integer ID of the referenced object,
        # 0 means no reference.
        return ObjectRef(round(float(value_)))
    else:
        return value_


def parse(raw_properties: etree.Element) -> Properties:
    final: Properties = {}

    for raw_property in raw_properties.findall("property"):
        type_ = raw_property.attrib.get("type")

        if type_ == "class":
            # Custom class properties hold their member values in a nested
            # <properties> element. Only members that were actually set are
            # saved; the project class definition supplies the rest.
            nested_element = raw_property.find("./properties")
            value: Property = (
                parse(nested_element) if nested_element is not None else {}
            )
        else:
            value_ = raw_property.attrib.get("value", raw_property.text)
            if value_ is None:
                continue
            value = _parse_property_value(raw_property, value_)

        final[raw_property.attrib["name"]] = value

    return final
