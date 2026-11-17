"""Utility Functions for PyTiled"""

import json
import xml.etree.ElementTree as etree
from pathlib import Path
from typing import Any, Dict, List

from pytiled_parser.common_types import Color


def parse_color(color: str) -> Color:
    """Convert Tiled color format into PyTiled's.

    Tiled's color format is #AARRGGBB and PyTiled's is an RGBA tuple.

    Args:
        color (str): Tiled formatted color string.
    Returns:
        :Color: Color object in the format that PyTiled understands.
    """
    # the actual part we care about is always an even number
    if len(color) % 2:
        # strip initial '#' character
        color = color[1:]

    if len(color) == 6:
        # full opacity if no alpha specified
        return Color(int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16), 255)
    elif len(color) == 8:
        return Color(
            int(color[2:4], 16),
            int(color[4:6], 16),
            int(color[6:8], 16),
            int(color[0:2], 16),
        )

    raise ValueError("Improperly formatted color passed to parse_color")


def check_format(file_path: Path, encoding: str) -> str:
    with open(file_path, encoding=encoding) as file:
        line = file.readline().rstrip().strip()
        if line[0] == "<":
            return "tmx"
        else:
            return "json"


def load_object_template(file_path: Path, encoding: str) -> Any:
    """Load an object template file, in either the TMX or JSON format.

    Args:
        file_path: Path to the template file.
        encoding: The character encoding to use when opening the file.

    Returns:
        A tuple of the raw template (an etree.Element for TMX templates, a dict
        for JSON templates), the raw tileset referenced by the template (or None),
        and the parent directory of that tileset (or None).

    Raises:
        FileNotFoundError: If the template file, or the tileset it references,
            does not exist.
        ValueError: If the template or its tileset is malformed.
    """
    if not file_path.is_file():
        raise FileNotFoundError(
            "Object template file does not exist or is not a file: "
            f"{file_path.absolute()}"
        )

    try:
        template_format = check_format(file_path, encoding)
    except (IndexError, UnicodeDecodeError) as exception:
        raise ValueError(
            "Could not determine the format of object template file: "
            f"{file_path.absolute()}"
        ) from exception

    new_tileset = None
    new_tileset_path = None

    if template_format == "tmx":
        try:
            with open(file_path, encoding=encoding) as template_file:
                template = etree.parse(template_file).getroot()
        except etree.ParseError as exception:
            raise ValueError(
                f"Invalid XML in object template file: {file_path.absolute()}"
            ) from exception

        if template.tag != "template" or template.find("./object") is None:
            raise ValueError(
                "Object template file is not valid, it must contain a 'template' "
                f"element with an 'object' element inside: {file_path.absolute()}"
            )

        tileset_element = template.find("./tileset")
        if tileset_element is not None:
            if tileset_element.attrib.get("source") is not None:
                tileset_path = Path(file_path.parent / tileset_element.attrib["source"])
                new_tileset = load_object_tileset(tileset_path, encoding)
                new_tileset_path = tileset_path.parent
            else:
                # The tileset is embedded directly inside the template
                new_tileset = tileset_element
                new_tileset_path = file_path.parent
    else:
        try:
            with open(file_path, encoding=encoding) as template_file:
                template = json.load(template_file)
        except ValueError as exception:
            raise ValueError(
                f"Invalid JSON in object template file: {file_path.absolute()}"
            ) from exception

        if not isinstance(template, dict) or "object" not in template:
            raise ValueError(
                "Object template file is not valid, it must contain an 'object' "
                f"key: {file_path.absolute()}"
            )

        if "tileset" in template:
            if template["tileset"].get("source") is not None:
                tileset_path = Path(
                    file_path.parent / template["tileset"]["source"]  # type: ignore
                )
                new_tileset = load_object_tileset(tileset_path, encoding)
                new_tileset_path = tileset_path.parent
            else:
                # The tileset is embedded directly inside the template
                new_tileset = template["tileset"]
                new_tileset_path = file_path.parent

    return (template, new_tileset, new_tileset_path)


def load_object_tileset(file_path: Path, encoding: str) -> Any:
    """Load a tileset referenced by an object template, in either format.

    Args:
        file_path: Path to the tileset file.
        encoding: The character encoding to use when opening the file.

    Raises:
        FileNotFoundError: If the tileset file does not exist.
        ValueError: If the tileset file is malformed.
    """
    if not file_path.is_file():
        raise FileNotFoundError(
            "Tileset file referenced by an object template does not exist or is "
            f"not a file: {file_path.absolute()}"
        )

    try:
        tileset_format = check_format(file_path, encoding)
    except (IndexError, UnicodeDecodeError) as exception:
        raise ValueError(
            "Could not determine the format of tileset file referenced by an "
            f"object template: {file_path.absolute()}"
        ) from exception

    with open(file_path, encoding=encoding) as tileset_file:
        if tileset_format == "tmx":
            try:
                return etree.parse(tileset_file).getroot()
            except etree.ParseError as exception:
                raise ValueError(
                    f"Invalid XML in tileset file: {file_path.absolute()}"
                ) from exception
        else:
            try:
                return json.load(tileset_file)
            except ValueError as exception:
                raise ValueError(
                    f"Invalid JSON in tileset file: {file_path.absolute()}"
                ) from exception


def _json_property_to_xml(raw_property: Dict[str, Any]) -> etree.Element:
    """Convert a JSON format property into an XML property element."""
    property_element = etree.Element("property")
    property_element.attrib["name"] = str(raw_property["name"])

    property_type = raw_property.get("type", "string")
    if property_type != "string":
        property_element.attrib["type"] = str(property_type)

    if property_type == "class":
        if raw_property.get("propertytype") is not None:
            property_element.attrib["propertytype"] = str(raw_property["propertytype"])
        children_element = etree.SubElement(property_element, "properties")
        for child in raw_property.get("properties", []):
            children_element.append(_json_property_to_xml(child))
        return property_element

    value = raw_property.get("value")
    if isinstance(value, bool):
        property_element.attrib["value"] = "true" if value else "false"
    elif value is not None:
        property_element.attrib["value"] = str(value)

    return property_element


def json_object_to_xml(raw_object: Dict[str, Any]) -> etree.Element:
    """Convert a JSON format object into an XML object element.

    This allows objects from JSON templates to be merged into TMX maps.

    Args:
        raw_object: The JSON format object to convert.

    Returns:
        An etree.Element representing the object in the TMX format.
    """
    object_element = etree.Element("object")

    for key, value in raw_object.items():
        if key in ("ellipse", "point", "polygon", "polyline", "text", "properties"):
            continue
        if key == "visible":
            object_element.attrib["visible"] = "1" if value else "0"
        elif key in (
            "id",
            "gid",
            "x",
            "y",
            "width",
            "height",
            "rotation",
            "name",
            "type",
            "class",
            "template",
        ):
            object_element.attrib[key] = str(value)

    if raw_object.get("ellipse"):
        etree.SubElement(object_element, "ellipse")

    if raw_object.get("point"):
        etree.SubElement(object_element, "point")

    for shape in ("polygon", "polyline"):
        if raw_object.get(shape):
            shape_element = etree.SubElement(object_element, shape)
            shape_element.attrib["points"] = " ".join(
                f"{point['x']},{point['y']}" for point in raw_object[shape]
            )

    if raw_object.get("text"):
        raw_text = raw_object["text"]
        text_element = etree.SubElement(object_element, "text")
        text_element.text = raw_text.get("text", "")

        for json_key, xml_key in (
            ("fontfamily", "fontfamily"),
            ("pixelsize", "pixelsize"),
            ("color", "color"),
            ("halign", "halign"),
            ("valign", "valign"),
        ):
            if raw_text.get(json_key) is not None:
                text_element.attrib[xml_key] = str(raw_text[json_key])

        for bool_key in ("bold", "italic", "kerning", "strikeout", "underline", "wrap"):
            if raw_text.get(bool_key) is not None:
                text_element.attrib[bool_key] = "1" if raw_text[bool_key] else "0"

    if raw_object.get("properties"):
        properties_element = etree.SubElement(object_element, "properties")
        for raw_property in raw_object["properties"]:
            properties_element.append(_json_property_to_xml(raw_property))

    return object_element


def _xml_property_to_json(property_element: etree.Element) -> Dict[str, Any]:
    """Convert an XML property element into a JSON format property."""
    property_type = property_element.attrib.get("type", "string")

    raw_property: Dict[str, Any] = {
        "name": property_element.attrib["name"],
        "type": property_type,
    }

    if property_type == "class":
        if property_element.attrib.get("propertytype") is not None:
            raw_property["propertytype"] = property_element.attrib["propertytype"]
        children_element = property_element.find("./properties")
        raw_property["properties"] = (
            [_xml_property_to_json(child) for child in children_element]
            if children_element is not None
            else []
        )
        return raw_property

    value: Any = property_element.attrib.get("value", property_element.text)

    if property_type == "bool":
        value = value == "true"
    elif property_type == "int":
        value = round(float(value))
    elif property_type == "float":
        value = float(value)
    # color, file, object and string values stay as strings, the JSON
    # property parser converts them to their final types.

    raw_property["value"] = value
    return raw_property


def xml_object_to_json(object_element: etree.Element) -> Dict[str, Any]:
    """Convert an XML object element into a JSON format object.

    This allows objects from TMX templates to be merged into JSON maps.
    Only attributes actually present on the element are included, so that
    values from deeper templates in a chain are not blocked by defaults.
    Missing values are filled in with Tiled's defaults after merging.

    Args:
        object_element: The XML object element to convert.

    Returns:
        A dict representing the object in the JSON format.
    """
    raw_object: Dict[str, Any] = {}

    for key, converter in (
        ("id", int),
        ("gid", int),
        ("x", float),
        ("y", float),
        ("width", float),
        ("height", float),
        ("rotation", float),
    ):
        if object_element.attrib.get(key) is not None:
            raw_object[key] = converter(object_element.attrib[key])

    if object_element.attrib.get("visible") is not None:
        raw_object["visible"] = bool(int(object_element.attrib["visible"]))

    for key in ("name", "type", "class", "template"):
        if object_element.attrib.get(key) is not None:
            raw_object[key] = object_element.attrib[key]

    if object_element.find("./ellipse") is not None:
        raw_object["ellipse"] = True

    if object_element.find("./point") is not None:
        raw_object["point"] = True

    for shape in ("polygon", "polyline"):
        shape_element = object_element.find(f"./{shape}")
        if shape_element is not None:
            points: List[Dict[str, float]] = []
            for raw_point in shape_element.attrib["points"].split(" "):
                point = raw_point.split(",")
                points.append({"x": float(point[0]), "y": float(point[1])})
            raw_object[shape] = points

    text_element = object_element.find("./text")
    if text_element is not None:
        raw_text: Dict[str, Any] = {"text": text_element.text or ""}

        if text_element.attrib.get("color") is not None:
            raw_text["color"] = text_element.attrib["color"]

        if text_element.attrib.get("fontfamily") is not None:
            raw_text["fontfamily"] = text_element.attrib["fontfamily"]

        if text_element.attrib.get("pixelsize") is not None:
            raw_text["pixelsize"] = float(text_element.attrib["pixelsize"])

        for bool_key in ("bold", "italic", "kerning", "strikeout", "underline", "wrap"):
            if text_element.attrib.get(bool_key) is not None:
                raw_text[bool_key] = bool(int(text_element.attrib[bool_key]))

        if text_element.attrib.get("halign") is not None:
            raw_text["halign"] = text_element.attrib["halign"]

        if text_element.attrib.get("valign") is not None:
            raw_text["valign"] = text_element.attrib["valign"]

        raw_object["text"] = raw_text

    properties_element = object_element.find("./properties")
    if properties_element is not None:
        raw_object["properties"] = [
            _xml_property_to_json(property_element)
            for property_element in properties_element.findall("property")
        ]

    return raw_object
