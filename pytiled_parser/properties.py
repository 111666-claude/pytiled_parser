"""Properties Module

This module defines types for Property objects.
For more about properties in Tiled maps see the below link:
https://doc.mapeditor.org/en/stable/manual/custom-properties/

The types defined in this module get added to other objects
such as Layers, Maps, Objects, etc
"""

from pathlib import Path
from typing import Dict, Union

from .common_types import Color

# Object reference properties ("type": "object" in Tiled) hold the integer ID
# of the referenced object. Nested custom class properties are represented as
# plain dictionaries of further properties.
Property = Union[int, float, Path, str, bool, Color, "ObjectRef", Dict[str, "Property"]]

Properties = Dict[str, Property]


class ObjectRef(int):
    """An object reference property value.

    Tiled object reference properties store the integer ID of the referenced
    object (0 means that nothing is referenced). This class subclasses ``int``
    so that the value behaves exactly like the referenced ID while still being
    distinguishable as an object reference when inspecting types.
    """
