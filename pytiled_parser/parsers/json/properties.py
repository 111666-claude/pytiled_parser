"""Property parsing for the JSON Map Format"""

from pathlib import Path
from typing import Any, List, Union, cast

from typing_extensions import TypedDict

from pytiled_parser.properties import ObjectRef, Properties, Property
from pytiled_parser.util import parse_color

RawValue = Union[float, str, bool, int, dict, list]


class RawProperty(TypedDict):
    """The keys and their values that appear in a Tiled JSON Property Object.

    Tiled Docs: https://doc.mapeditor.org/en/stable/reference/json-map-format/#property
    """

    name: str
    type: str
    value: RawValue
    propertytype: str


def parse(raw_properties: List[RawProperty]) -> Properties:
    """Parse a list of `RawProperty` objects into `Properties`.

    Args:
        raw_properties: The list or dict of `RawProperty` objects to parse. The dict type is supported for parsing legacy Tiled dungeon files.

    Returns:
        Properties: The parsed `Property` objects.
    """

    final: Properties = {}
    value: Property

    if isinstance(raw_properties, dict):
        for name, value in raw_properties.items():
            final[name] = value
    else:
        for raw_property in raw_properties:
            property_type = raw_property["type"]
            if property_type == "file":
                value = Path(cast(str, raw_property["value"]))
            elif property_type == "color":
                value = parse_color(cast(str, raw_property["value"]))
            elif property_type == "int":
                # Malformed values are kept raw here; the project merge step
                # reports them as type conflicts, including their object
                # location. Valid numeric values are normalized as before.
                raw_int_value = raw_property["value"]
                value = (
                    round(cast(float, raw_int_value))
                    if isinstance(raw_int_value, (int, float))
                    and not isinstance(raw_int_value, bool)
                    else cast(Any, raw_int_value)
                )
            elif property_type == "float":
                raw_float_value = raw_property["value"]
                value = (
                    float(cast(float, raw_float_value))
                    if isinstance(raw_float_value, (int, float))
                    and not isinstance(raw_float_value, bool)
                    else cast(Any, raw_float_value)
                )
            elif property_type == "object":
                # Object references hold the integer ID of the referenced
                # object, 0 means no reference.
                value = ObjectRef(cast(int, raw_property["value"]))
            elif property_type == "class":
                # Custom class properties serialize as a plain object holding
                # the member names that were actually set. Member value types
                # are derived from the project class definition when the
                # project is applied; raw JSON primitives are kept here.
                raw_value = cast(dict, raw_property["value"])
                value = dict(raw_value)
            else:
                value = cast(Any, raw_property["value"])
            final[raw_property["name"]] = value

    return final
