import os
import xml.etree.ElementTree as etree
from pathlib import Path
from typing import Any, Callable, Optional, Tuple

from pytiled_parser import diagnostics
from pytiled_parser.common_types import OrderedPair, Size
from pytiled_parser.exception import (
    CircularReferenceError,
    FieldTypeError,
    InvalidFieldValueError,
    MissingFieldError,
    TemplateError,
    UnsupportedTemplateError,
)
from pytiled_parser.parsers.tmx.properties import parse as parse_properties
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

_MAX_TEMPLATE_DEPTH = 32


def _referenced_from() -> str:
    """Describe where the referenced template was included from."""

    frame = diagnostics.current_frame()
    document = frame.document
    location = document.display_file if document is not None else "<unknown>"
    if frame.field_prefix:
        return "{}: {}".format(location, frame.field_prefix)
    return "{}: template".format(location)


def _parse_common(raw_object: etree.Element) -> TiledObject:
    """Create an Object containing all the attributes common to all types of objects.

    Args:
        raw_object: XML Element to get common attributes from

    Returns:
        Object: The attributes in common of all types of objects
    """
    object_id = diagnostics.xml_int_attr(raw_object, "id")
    x = diagnostics.xml_float_attr(raw_object, "x")
    y = diagnostics.xml_float_attr(raw_object, "y")

    common = TiledObject(
        id=object_id,  # type: ignore[arg-type]
        coordinates=OrderedPair(x, y),  # type: ignore[arg-type]
    )

    if raw_object.attrib.get("width") is not None:
        common.size = Size(
            diagnostics.xml_float_attr(raw_object, "width"),  # type: ignore[arg-type]
            diagnostics.xml_float_attr(raw_object, "height"),  # type: ignore[arg-type]
        )

    if raw_object.attrib.get("visible") is not None:
        common.visible = diagnostics.xml_bool_attr(  # type: ignore[assignment]
            raw_object, "visible"
        )

    if raw_object.attrib.get("rotation") is not None:
        common.rotation = diagnostics.xml_float_attr(  # type: ignore[assignment]
            raw_object, "rotation"
        )

    if raw_object.attrib.get("name") is not None:
        common.name = raw_object.attrib["name"]

    if raw_object.attrib.get("type") is not None:
        common.class_ = raw_object.attrib["type"]

    if raw_object.attrib.get("class") is not None:
        common.class_ = raw_object.attrib["class"]

    properties_element = raw_object.find("./properties")
    if properties_element is not None:
        common.properties = parse_properties(properties_element)

    return common


def _parse_ellipse(raw_object: etree.Element) -> Ellipse:
    return Ellipse(**_parse_common(raw_object).__dict__)


def _parse_rectangle(raw_object: etree.Element) -> Rectangle:
    return Rectangle(**_parse_common(raw_object).__dict__)


def _parse_point(raw_object: etree.Element) -> Point:
    return Point(**_parse_common(raw_object).__dict__)


def _parse_points(raw_object: etree.Element, tag: str) -> list:
    points = []
    points_element = raw_object.find("./{}".format(tag))
    if points_element is not None:
        raw_points = diagnostics.xml_attr(
            points_element, "points", field="{}.points".format(tag)
        )
        assert raw_points is not None
        for index, raw_point in enumerate(raw_points.split(" ")):
            if not raw_point:
                continue
            coordinates = raw_point.split(",")
            if len(coordinates) != 2:
                raise diagnostics.xml_error(
                    InvalidFieldValueError,
                    "Invalid point {!r} at index {} in <{}>".format(
                        raw_point, index, tag
                    ),
                    element=points_element,
                    field="{}.points[{}]".format(tag, index),
                    reason="points must be written as 'x,y'",
                )
            try:
                points.append(OrderedPair(float(coordinates[0]), float(coordinates[1])))
            except (TypeError, ValueError) as error:
                raise diagnostics.xml_error(
                    FieldTypeError,
                    "Invalid point {!r} at index {} in <{}>".format(
                        raw_point, index, tag
                    ),
                    element=points_element,
                    field="{}.points[{}]".format(tag, index),
                    reason=str(error),
                ) from error
    return points


def _parse_polygon(raw_object: etree.Element) -> Polygon:
    return Polygon(
        points=_parse_points(raw_object, "polygon"),
        **_parse_common(raw_object).__dict__,
    )


def _parse_polyline(raw_object: etree.Element) -> Polyline:
    return Polyline(
        points=_parse_points(raw_object, "polyline"),
        **_parse_common(raw_object).__dict__,
    )


def _parse_tile(
    raw_object: etree.Element,
    new_tileset: Optional[etree.Element] = None,
    new_tileset_path: Optional[Path] = None,
) -> Tile:
    return Tile(
        gid=diagnostics.xml_int_attr(raw_object, "gid"),  # type: ignore[arg-type]
        new_tileset=new_tileset,
        new_tileset_path=new_tileset_path,
        **_parse_common(raw_object).__dict__,
    )


def _parse_text(raw_object: etree.Element) -> Text:
    text_element = raw_object.find("./text")

    if text_element is not None:
        text = text_element.text

        if not text:
            text = ""
        text_object = Text(text=text, **_parse_common(raw_object).__dict__)

        if text_element.attrib.get("color") is not None:
            color = text_element.attrib["color"]
            try:
                text_object.color = parse_color(color)
            except InvalidFieldValueError as error:
                raise diagnostics.xml_error(
                    InvalidFieldValueError,
                    "Invalid text color {!r}".format(color),
                    element=text_element,
                    field="text.color",
                    reason=error.reason or str(error),
                ) from error

        if text_element.attrib.get("fontfamily") is not None:
            text_object.font_family = text_element.attrib["fontfamily"]

        if text_element.attrib.get("pixelsize") is not None:
            text_object.font_size = diagnostics.xml_float_attr(  # type: ignore[assignment]
                text_element, "pixelsize", field="text"
            )

        if text_element.attrib.get("bold") is not None:
            text_object.bold = diagnostics.xml_bool_attr(  # type: ignore[assignment]
                text_element, "bold", field="text"
            )

        if text_element.attrib.get("italic") is not None:
            text_object.italic = diagnostics.xml_bool_attr(  # type: ignore[assignment]
                text_element, "italic", field="text"
            )

        if text_element.attrib.get("kerning") is not None:
            text_object.kerning = diagnostics.xml_bool_attr(  # type: ignore[assignment]
                text_element, "kerning", field="text"
            )

        if text_element.attrib.get("strikeout") is not None:
            text_object.strike_out = diagnostics.xml_bool_attr(  # type: ignore[assignment]
                text_element, "strikeout", field="text"
            )

        if text_element.attrib.get("underline") is not None:
            text_object.underline = diagnostics.xml_bool_attr(  # type: ignore[assignment]
                text_element, "underline", field="text"
            )

        if text_element.attrib.get("halign") is not None:
            text_object.horizontal_align = text_element.attrib["halign"]

        if text_element.attrib.get("valign") is not None:
            text_object.vertical_align = text_element.attrib["valign"]

        if text_element.attrib.get("wrap") is not None:
            text_object.wrap = diagnostics.xml_bool_attr(  # type: ignore[assignment]
                text_element, "wrap", field="text"
            )

        return text_object

    # A <text> object with no <text> child is invalid; report it precisely
    # instead of raising an UnboundLocalError.
    raise diagnostics.xml_error(
        MissingFieldError,
        "Text object has no <text> child",
        element=raw_object,
        field="text",
        reason="text objects require a <text> element",
    )


def _get_parser(raw_object: etree.Element) -> Callable[[etree.Element], TiledObject]:
    if raw_object.find("./ellipse") is not None:
        return _parse_ellipse

    if raw_object.find("./point") is not None:
        return _parse_point

    if raw_object.find("./polygon") is not None:
        return _parse_polygon

    if raw_object.find("./polyline") is not None:
        return _parse_polyline

    if raw_object.find("./text") is not None:
        return _parse_text

    # If it's none of the above, rectangle is the only one left.
    # Rectangle is the only object which has no properties to signify that.
    return _parse_rectangle


def _load_template(
    reference: str,
    parent_dir: Path,
    *,
    element: etree.Element,
    chain: Tuple[str, ...] = (),
) -> Tuple[etree.Element, Any, Optional[Path], Tuple[str, ...], str, Any]:
    """Load a TMX object template, detecting cycles and unsupported formats."""

    from pytiled_parser.diagnostics import JsonDocument, XmlDocument

    absolute = os.path.normcase(os.path.abspath(os.fspath(parent_dir / reference)))

    if absolute in chain:
        cycle = list(chain[chain.index(absolute) :]) + [absolute]
        display_cycle = " -> ".join(
            diagnostics.display_reference(item, parent_dir) for item in cycle
        )
        raise diagnostics.xml_error(
            CircularReferenceError,
            "Object templates reference each other in a cycle: {}".format(
                display_cycle
            ),
            element=element,
            field="template",
            reason="circular template reference",
        )

    if len(chain) >= _MAX_TEMPLATE_DEPTH:
        raise diagnostics.xml_error(
            CircularReferenceError,
            "Object template nesting is deeper than {} levels".format(
                _MAX_TEMPLATE_DEPTH
            ),
            element=element,
            field="template",
            reason="template nesting too deep",
        )

    template_path = Path(parent_dir / reference)
    display = diagnostics.display_reference(reference, parent_dir)
    diagnostics.check_external_file(
        template_path,
        reference,
        base_dir=parent_dir,
        element=element,
        field="template",
    )

    document = load_document(template_path, display=display)

    if isinstance(document, JsonDocument):
        raise diagnostics.xml_error(
            UnsupportedTemplateError,
            "Loading JSON object templates inside a TMX map is currently not "
            "supported, but will be in a future release.",
            element=element,
            field="template",
            reason="template format does not match map format",
        )

    assert isinstance(document, XmlDocument)

    if document.root.tag != "template":
        raise diagnostics.xml_error(
            TemplateError,
            "File '{}' is not a Tiled object template".format(display),
            element=element,
            field="template",
            reason="expected a <template> root element",
        )

    new_object = document.root.find("./object")
    if new_object is None:
        raise diagnostics.xml_error(
            MissingFieldError,
            "Object template '{}' has no <object> element".format(display),
            element=element,
            field="template",
            reason="templates require an <object> element",
        )

    new_tileset = None
    new_tileset_path = None
    tileset_element = document.root.find("./tileset")
    if tileset_element is not None:
        source = diagnostics.xml_attr(tileset_element, "source", field="tileset.source")
        assert source is not None
        tileset_path = Path(template_path.parent / source)
        diagnostics.check_external_file(
            tileset_path,
            source,
            base_dir=template_path.parent,
            element=tileset_element,
            field="source",
        )
        tileset_document = load_document(
            tileset_path,
            display=diagnostics.display_reference(source, template_path.parent),
        )
        if isinstance(tileset_document, XmlDocument):
            new_tileset = tileset_document.root
        else:
            new_tileset = tileset_document.data
        new_tileset_path = tileset_path.parent

    return (
        new_object,
        new_tileset,
        new_tileset_path,
        chain + (absolute,),
        display,
        document,
    )


def _apply_template(
    raw_object: etree.Element,
    parent_dir: Path,
    *,
    chain: Tuple[str, ...],
) -> Tuple[etree.Element, Any, Optional[Path], Tuple[str, ...], str, Any]:
    """Resolve an object's template chain and apply the overrides."""

    reference = raw_object.attrib["template"]
    (
        new_object,
        new_tileset,
        new_tileset_path,
        new_chain,
        display,
        template_document,
    ) = _load_template(reference, parent_dir, element=raw_object, chain=chain)

    # Nested templates: the loaded object may itself extend another template.
    # Resolve the base first so overrides from this template and the instance
    # both apply on top of it.
    nested_reference = new_object.attrib.get("template")
    if nested_reference is not None:

        # Resolve the nested reference in the context of the template file so
        # its position and cycle chain are attributed correctly.
        with diagnostics.enter(
            document=template_document,
            field_prefix="object",
            template_chain=diagnostics.current_frame().template_chain + (display,),
            template_files=new_chain,
            referenced_from=_referenced_from(),
        ):
            (
                base_object,
                base_tileset,
                base_tileset_path,
                base_chain,
                base_display,
                base_document,
            ) = _apply_template(
                new_object,
                Path(parent_dir / reference).parent,
                chain=new_chain,
            )
        # Copy this template's own attributes/elements over the base object.
        for key, val in new_object.attrib.items():
            if key == "template":
                continue
            base_object.attrib[key] = val
        _merge_child_properties(base_object, new_object)
        new_object = base_object
        if new_tileset is None:
            new_tileset = base_tileset
            new_tileset_path = base_tileset_path
        new_chain = base_chain
        display = "{} -> {}".format(display, base_display)
        template_document = base_document

    for key, val in raw_object.attrib.items():
        if key == "template":
            continue
        new_object.attrib[key] = val
    _merge_child_properties(new_object, raw_object)

    return (
        new_object,
        new_tileset,
        new_tileset_path,
        new_chain,
        display,
        template_document,
    )


def _merge_child_properties(
    base_object: etree.Element, child_object: etree.Element
) -> None:
    """Merge ``<properties>`` from *child_object* into *base_object*.

    Values set on the child (the object instance or a more derived template)
    take precedence over values inherited from the base template.
    """
    properties_element = child_object.find("./properties")
    if properties_element is None:
        return
    base_properties = base_object.find("./properties")
    if base_properties is None:
        base_object.append(properties_element)
        return

    child_by_name = {
        prop.attrib.get("name"): prop
        for prop in properties_element
        if prop.attrib.get("name") is not None
    }

    for existing in list(base_properties):
        name = existing.attrib.get("name")
        if name in child_by_name:
            base_properties.remove(existing)
    for prop in properties_element:
        base_properties.append(prop)


def parse(
    raw_object: etree.Element,
    parent_dir: Optional[Path] = None,
    *,
    index: Optional[int] = None,
) -> TiledObject:
    """Parse the raw object into a pytiled_parser version

    Args:
        raw_object: XML Element that is to be parsed.
        parent_dir: The parent directory that the map file is in.
        index: Index of this object within its layer, used for diagnostics.

    Returns:
        TiledObject: A parsed Object.

    Raises:
        TemplateError: When a template can not be resolved.
        CircularReferenceError: When templates reference each other in a cycle.
    """
    new_tileset = None
    new_tileset_path = None

    description = diagnostics.describe_xml_object(raw_object)
    if description.startswith("<") and index is not None:
        description = "<object index {}>".format(index)

    frame = diagnostics.current_frame()
    with diagnostics.enter(
        object=description,
        layer=frame.layer,
    ):
        if raw_object.attrib.get("template"):
            if not parent_dir:
                raise diagnostics.xml_error(
                    TemplateError,
                    "A parent directory must be specified when using object "
                    "templates.",
                    element=raw_object,
                    field="template",
                    reason="template resolution needs the map directory",
                )

            (
                resolved_object,
                new_tileset,
                new_tileset_path,
                template_chain,
                display,
                template_document,
            ) = _apply_template(
                raw_object,
                parent_dir,
                chain=frame.template_files,
            )

            # Subsequent errors belong to the template file.  Entering its
            # document context makes element positions resolve there, and the
            # template chain records how we got here.
            current = diagnostics.current_frame()
            with diagnostics.enter(
                document=template_document,
                field_prefix="object",
                template_chain=current.template_chain + (display,),
                template_files=template_chain,
                parent_dir=parent_dir,
                referenced_from=_referenced_from(),
            ):
                if resolved_object.attrib.get("gid"):
                    return _parse_tile(resolved_object, new_tileset, new_tileset_path)
                return _get_parser(resolved_object)(resolved_object)

        if raw_object.attrib.get("gid"):
            return _parse_tile(raw_object, new_tileset, new_tileset_path)

        return _get_parser(raw_object)(raw_object)
