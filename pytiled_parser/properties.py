"""Properties Module

This module defines types for Property objects.
For more about properties in Tiled maps see the below link:
https://doc.mapeditor.org/en/stable/manual/custom-properties/

The types defined in this module get added to other objects
such as Layers, Maps, Objects, etc
"""

from pathlib import Path
from typing import Any, Dict, Optional, Union

from .common_types import Color

class ClassProperty(dict):
    def __init__(self, propertytype:str, *args, **kwargs):
        self.propertytype = propertytype or ''
        dict.__init__(self, *args, **kwargs)


class ObjectRef:
    """The value of an `object` type custom property.

    In Tiled, an `object` type property stores the ID of another object in the
    same map. The raw value from the map file is preserved unmodified in
    `value`, so nothing is lost by parsing. To turn these values into the
    actual objects they point to, use
    [resolve_object_references][pytiled_parser.object_ref.resolve_object_references].

    Attributes:
        value: The raw value of the property as it appeared in the map file.
    """

    def __init__(self, value: Any) -> None:
        self.value = value

    @property
    def id(self) -> Optional[int]:
        """The referenced object ID, or None if the raw value is not a valid ID."""
        if isinstance(self.value, bool):
            return None
        try:
            as_float = float(self.value)
        except (TypeError, ValueError):
            return None
        if not as_float.is_integer():
            return None
        return int(as_float)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, ObjectRef):
            return self.value == other.value
        return bool(self.value == other)

    def __hash__(self) -> int:
        return hash(self.value)

    def __repr__(self) -> str:
        return f"ObjectRef({self.value!r})"


Property = Union[int, float, Path, str, bool, Color, ClassProperty, ObjectRef]

Properties = Dict[str, Property]
