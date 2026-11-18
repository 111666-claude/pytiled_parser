"""Tiled project (``.tiled-project``) parsing and effective property resolution.

A Tiled project defines custom enum and custom class types. Custom classes carry
default values for their members. When a map, layer, tile or object is assigned
one of those classes, its *effective* properties are the result of merging the
class defaults with the values explicitly stored in the map, with explicit
values taking precedence at every level of nesting.

This module is format agnostic. Both the TMX and JSON parsers first produce the
same in-memory object model, and :func:`apply_project` then walks that model to
fill in class defaults, validate enum values and check property types, which
keeps the results of the two formats identical.

See the Tiled manual:
https://doc.mapeditor.org/en/stable/manual/custom-properties/
"""

import copy
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, cast

import attr

from .common_types import Color
from .exception import (
    CircularClassReferenceError,
    InvalidEnumValueError,
    InvalidProjectError,
    InvalidPropertyPathError,
    ProjectNotFoundError,
    PropertyTypeConflictError,
    UnknownClassError,
)
from .properties import ObjectRef, Properties, Property
from .util import parse_color

# The built in property types Tiled knows about. "class" and "enum" are resolved
# through the project's custom types, everything else maps directly onto a
# Python type.
_SCALAR_TYPES = {"string", "int", "float", "bool", "color", "file", "object"}


@attr.s(auto_attribs=True, kw_only=True)
class EnumType:
    """A custom enum type defined in a Tiled project.

    Attributes:
        name: Name of the enum.
        storage_type: Either ``"string"`` (the default) or ``"int"``.
        values: The allowed enum member names, in declaration order.
        values_as_flags: Whether multiple values may be combined as flags.
    """

    name: str
    storage_type: str = "string"
    values: List[str] = attr.Factory(list)
    values_as_flags: bool = False

    def validate(self, value: Any, location: str) -> None:
        """Check that ``value`` is a legal value for this enum.

        Args:
            value: The candidate enum value.
            location: Human readable location of the property being checked,
                included verbatim in any raised error.

        Raises:
            InvalidEnumValueError: If the value is not legal for this enum.
            PropertyTypeConflictError: If the value has the wrong Python type.
        """
        if self.storage_type == "int":
            if isinstance(value, bool) or not isinstance(value, int):
                raise PropertyTypeConflictError(
                    f"{location}: enum '{self.name}' uses integer storage but "
                    f"got value {value!r} of type {type(value).__name__}"
                )
            if self.values_as_flags:
                valid_mask = (1 << len(self.values)) - 1
                if value < 0 or value & ~valid_mask:
                    raise InvalidEnumValueError(
                        f"{location}: {value!r} is not a valid flags combination "
                        f"for enum '{self.name}' (valid values: {self.values})"
                    )
            elif value < 0 or value >= len(self.values):
                raise InvalidEnumValueError(
                    f"{location}: {value!r} is not a valid value for enum "
                    f"'{self.name}' (valid values: {self.values})"
                )
            return

        if not isinstance(value, str):
            raise PropertyTypeConflictError(
                f"{location}: enum '{self.name}' uses string storage but got "
                f"value {value!r} of type {type(value).__name__}"
            )

        if self.values_as_flags:
            tokens = [token for token in value.split(",") if token]
            for token in tokens:
                if token not in self.values:
                    raise InvalidEnumValueError(
                        f"{location}: {token!r} is not a valid flag value for "
                        f"enum '{self.name}' (valid values: {self.values})"
                    )
        elif value not in self.values:
            raise InvalidEnumValueError(
                f"{location}: {value!r} is not a valid value for enum "
                f"'{self.name}' (valid values: {self.values})"
            )


@attr.s(auto_attribs=True, kw_only=True)
class ClassMember:
    """A single member (field) of a custom class.

    Attributes:
        name: Name of the member.
        type: The Tiled type name. One of the built in types, ``"class"`` or
            ``"enum"``.
        property_type: For ``class`` and ``enum`` members, the name of the
            custom type referenced. ``None`` for built in types.
        default: The member's default value as defined in the project, already
            converted to the same Python types used for map properties.
    """

    name: str
    type: str
    property_type: Optional[str] = None
    default: Optional[Property] = None


@attr.s(auto_attribs=True, kw_only=True)
class ClassType:
    """A custom class type defined in a Tiled project.

    Attributes:
        name: Name of the class.
        color: The display color defined for the class, if any.
        use_as: The scopes the class is usable for (``"map"``, ``"layer"``,
            ``"object"``, ``"tile"``, ``"tileset"``, ...).
        members: Ordered mapping of member name to its :class:`ClassMember`.
    """

    name: str
    color: Optional[Color] = None
    use_as: List[str] = attr.Factory(list)
    members: Dict[str, ClassMember] = attr.Factory(dict)


@attr.s(auto_attribs=True, kw_only=True)
class Project:
    """A parsed Tiled project.

    Attributes:
        project_file: Path the project was loaded from.
        classes: Custom class types keyed by name.
        enums: Custom enum types keyed by name.
    """

    project_file: Path
    classes: Dict[str, ClassType] = attr.Factory(dict)
    enums: Dict[str, EnumType] = attr.Factory(dict)

    @property
    def base_dir(self) -> Path:
        """Directory containing the project file.

        Relative ``file`` property defaults in the project are interpreted
        relative to this directory.
        """
        return self.project_file.parent


def _coerce_scalar(
    value: Any,
    member: ClassMember,
    location: str,
    project: "Project",
    check_paths: bool = True,
    base_dir: Optional[Path] = None,
) -> Property:
    """Normalize a scalar value so TMX and JSON input produce identical output.

    The TMX parser already converts colors, paths and object references into
    their typed representations, whereas the JSON parser keeps class member
    values as raw JSON primitives (their types are only known from the project
    definition). This function accepts either representation and returns the
    canonical typed value.

    Args:
        check_paths: When ``False``, ``file`` member values are converted to
            paths without checking that they exist. Used while resolving the
            project's own member defaults, where locations refer to concrete
            map objects that do not exist yet.
        base_dir: Directory relative paths are resolved against. Defaults to
            the project directory, but map/tile object properties pass their
            own containing file's directory instead, matching Tiled's
            behavior for explicit ``file`` properties.
    """
    if base_dir is None:
        base_dir = project.base_dir
    type_name = member.type

    if type_name == "string":
        if not isinstance(value, str):
            raise PropertyTypeConflictError(
                f"{location}: expected a string but got {value!r} "
                f"of type {type(value).__name__}"
            )
        return value

    if type_name == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise PropertyTypeConflictError(
                f"{location}: expected an int but got {value!r} "
                f"of type {type(value).__name__}"
            )
        return value

    if type_name == "float":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise PropertyTypeConflictError(
                f"{location}: expected a float but got {value!r} "
                f"of type {type(value).__name__}"
            )
        return float(value)

    if type_name == "bool":
        if not isinstance(value, bool):
            raise PropertyTypeConflictError(
                f"{location}: expected a bool but got {value!r} "
                f"of type {type(value).__name__}"
            )
        return value

    if type_name == "color":
        if isinstance(value, Color):
            return value
        if not isinstance(value, str):
            raise PropertyTypeConflictError(
                f"{location}: expected a color string but got {value!r} "
                f"of type {type(value).__name__}"
            )
        try:
            return parse_color(value)
        except ValueError:
            raise PropertyTypeConflictError(
                f"{location}: {value!r} is not a valid Tiled color"
            )

    if type_name == "file":
        if isinstance(value, Path):
            path = value
        elif isinstance(value, str):
            path = Path(value)
        else:
            raise PropertyTypeConflictError(
                f"{location}: expected a file path but got {value!r} "
                f"of type {type(value).__name__}"
            )

        if not path.is_absolute():
            resolved = (base_dir / path).resolve()
        else:
            resolved = path.resolve()
        if check_paths and not resolved.exists():
            raise InvalidPropertyPathError(
                f"{location}: file property '{member.name}' references "
                f"'{path}' which does not exist ({resolved})"
            )
        return path

    if type_name == "object":
        if isinstance(value, bool) or not isinstance(value, int):
            raise PropertyTypeConflictError(
                f"{location}: expected an object reference (int) but got "
                f"{value!r} of type {type(value).__name__}"
            )
        return ObjectRef(value)

    raise InvalidProjectError(
        f"{location}: class member '{member.name}' has unknown built in "
        f"type '{type_name}'"
    )


def _coerce_value(
    value: Any,
    member: ClassMember,
    location: str,
    project: "Project",
    resolution_stack: Optional[List[str]] = None,
    check_paths: bool = True,
    base_dir: Optional[Path] = None,
) -> Property:
    """Convert a raw or partially typed value for ``member`` into its final type.

    Handles enum validation and recursion into nested custom classes in
    addition to the built in scalar types.
    """
    if resolution_stack is None:
        resolution_stack = []

    if member.type == "enum":
        enum = project.enums.get(member.property_type or "")
        if enum is None:
            raise InvalidProjectError(
                f"{location}: member '{member.name}' references unknown enum "
                f"type '{member.property_type}'"
            )
        enum.validate(value, location)
        return value

    if member.type == "class":
        class_name = member.property_type or ""
        class_type = project.classes.get(class_name)
        if class_type is None:
            raise InvalidProjectError(
                f"{location}: member '{member.name}' references unknown class "
                f"type '{class_name}'"
            )
        if not isinstance(value, dict):
            raise PropertyTypeConflictError(
                f"{location}: expected a class '{class_name}' value (object) "
                f"but got {value!r} of type {type(value).__name__}"
            )
        return _merge_class(
            class_type,
            value,
            location,
            project,
            list(resolution_stack),
            check_paths=check_paths,
            base_dir=base_dir,
        )

    return _coerce_scalar(
        value,
        member,
        location,
        project,
        check_paths=check_paths,
        base_dir=base_dir,
    )


def _merge_class(
    class_type: ClassType,
    explicit: Properties,
    location: str,
    project: "Project",
    resolution_stack: List[str],
    check_paths: bool = True,
    base_dir: Optional[Path] = None,
) -> Properties:
    """Recursively merge ``explicit`` values onto the class member defaults.

    Nested class properties are merged field by field rather than replaced
    wholesale, so overriding one field of a nested class keeps the remaining
    default fields. Every value is validated against its declared member type.
    """
    if class_type.name in resolution_stack:
        cycle = " -> ".join(resolution_stack + [class_type.name])
        raise CircularClassReferenceError(
            f"{location}: circular custom class reference detected: {cycle}"
        )
    resolution_stack = resolution_stack + [class_type.name]

    result: Properties = copy.deepcopy(
        {
            name: cast(Property, member.default)
            for name, member in class_type.members.items()
        }
    )

    for key, raw_value in explicit.items():
        class_member = class_type.members.get(key)
        member_location = f"{location}.{key}"
        if class_member is None:
            # The member was removed from the project definition after the map
            # was saved. Tiled ignores such members, but preserving the raw
            # value avoids silently dropping user data.
            result[key] = raw_value
            continue

        result[key] = _coerce_value(
            raw_value,
            class_member,
            member_location,
            project,
            resolution_stack,
            check_paths=check_paths,
            base_dir=base_dir,
        )

    if check_paths:
        # Default file references were converted to paths while loading the
        # project without checking existence. Validate the defaults that
        # survived the merge (i.e. were not explicitly overridden) now,
        # recursively for nested classes. Defaults are authored in the
        # project, so they stay relative to the project directory; explicit
        # values were already validated above against the object's directory.
        _validate_class_file_paths(class_type, result, explicit, location, project)

    return result


def _validate_class_file_paths(
    class_type: ClassType,
    values: Properties,
    explicit: Properties,
    location: str,
    project: "Project",
) -> None:
    """Check existence of surviving default ``file`` members, recursively."""
    for name, member in class_type.members.items():
        if name in explicit:
            # Explicitly overridden, its value was validated against the
            # object's own directory during the merge.
            continue

        value = values.get(name)
        member_location = f"{location}.{name}"

        if member.type == "file":
            _coerce_scalar(
                value,
                member,
                member_location,
                project,
                base_dir=project.base_dir,
            )
        elif member.type == "class":
            nested_class = project.classes.get(member.property_type or "")
            if nested_class is not None and isinstance(value, dict):
                nested_explicit = explicit.get(name)
                if not isinstance(nested_explicit, dict):
                    nested_explicit = {}
                _validate_class_file_paths(
                    nested_class,
                    value,
                    nested_explicit,
                    member_location,
                    project,
                )


def effective_properties(
    project: "Project",
    class_name: str,
    explicit: Optional[Properties],
    location: str,
    base_dir: Optional[Path] = None,
) -> Properties:
    """Compute the effective properties for an object of ``class_name``.

    The class's member defaults form the base; ``explicit`` values override
    them, including recursively for nested class members.

    Args:
        project: The project defining the custom class.
        class_name: The class assigned to the map/layer/tile/object.
        explicit: The properties explicitly set on the object, if any.
        location: Human readable location used in error messages.
        base_dir: Directory used to resolve relative ``file`` values of
            explicit properties. Defaults to the project directory.

    Raises:
        UnknownClassError: If ``class_name`` is not defined in the project.
    """
    class_type = project.classes.get(class_name)
    if class_type is None:
        raise UnknownClassError(
            f"{location}: references unknown custom class '{class_name}' "
            f"defined in project '{project.project_file}'"
        )

    merged = _merge_class(
        class_type,
        explicit or {},
        location,
        project,
        [],
        base_dir=base_dir,
    )

    # Preserve explicit properties that are not part of the class definition.
    for key, value in (explicit or {}).items():
        if key not in class_type.members:
            merged[key] = value

    return merged


def _parse_member_default(
    member: ClassMember, project: "Project", class_name: str
) -> Property:
    """Resolve a project class member's raw default value into its final type."""
    location = (
        f"project '{project.project_file}', class '{class_name}', "
        f"member '{member.name}'"
    )
    # Default file references are only validated when applied to a concrete
    # map object, so error messages can name the object's location.
    return _coerce_value(
        member.default,
        member,
        location,
        project,
        [class_name],
        check_paths=False,
    )


def _check_class_cycles(project: "Project") -> None:
    """Fail fast if class members reference each other, directly or indirectly.

    Cycles are detected from the member declarations themselves rather than
    from concrete values, so a project whose empty default values would never
    traverse the cycle is still rejected at load time.
    """

    def visit(class_name: str, stack: List[str]) -> None:
        if class_name in stack:
            cycle = " -> ".join(stack + [class_name])
            raise CircularClassReferenceError(
                f"Tiled project '{project.project_file}' contains a circular "
                f"custom class reference: {cycle}"
            )
        class_type = project.classes.get(class_name)
        if class_type is None:
            return
        for member in class_type.members.values():
            if member.type == "class":
                visit(member.property_type or "", stack + [class_name])

    for class_name in project.classes:
        visit(class_name, [])


def parse_project(file: Path) -> Project:
    """Load a ``.tiled-project`` file and its custom types.

    Both the modern ``propertyTypes`` key and the older ``projectTypes`` key
    are supported. Member defaults are resolved in a second pass so that class
    and enum members may reference types declared anywhere in the file.

    Args:
        file: Path to the ``.tiled-project`` file.

    Raises:
        ProjectNotFoundError: If the file does not exist.
        InvalidProjectError: If the file is not valid JSON or its type
            definitions are malformed.
        CircularClassReferenceError: If class references form a cycle.
    """
    file = Path(file)
    if not file.exists():
        raise ProjectNotFoundError(f"Tiled project file does not exist: '{file}'")

    try:
        with open(file, encoding="utf-8") as project_file:
            raw = json.load(project_file)
    except (json.JSONDecodeError, OSError) as error:
        raise InvalidProjectError(
            f"Could not parse Tiled project file '{file}': {error}"
        )

    if not isinstance(raw, dict):
        raise InvalidProjectError(
            f"Tiled project file '{file}' must contain a JSON object"
        )

    # Modern Tiled writes "propertyTypes"; older Tiled versions used
    # "projectTypes". Accept either.
    raw_types = raw.get("propertyTypes")
    if raw_types is None:
        raw_types = raw.get("projectTypes", [])
    if not isinstance(raw_types, list):
        raise InvalidProjectError(
            f"Tiled project file '{file}' has an invalid 'propertyTypes' section"
        )

    project = Project(project_file=file)

    # First pass: create all enum and class definitions without resolving
    # member values yet, so forward references between types are allowed.
    pending_classes: Dict[str, List[Dict[str, Any]]] = {}
    for entry in raw_types:
        if not isinstance(entry, dict):
            raise InvalidProjectError(
                f"Tiled project file '{file}' contains a type definition that "
                f"is not a JSON object: {entry!r}"
            )

        type_name = entry.get("type")
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            raise InvalidProjectError(
                f"Tiled project file '{file}' contains a type definition "
                f"without a valid 'name': {entry!r}"
            )

        if type_name == "enum":
            if name in project.enums or name in project.classes:
                raise InvalidProjectError(
                    f"Tiled project file '{file}' defines duplicate type "
                    f"name '{name}'"
                )
            storage = entry.get("storageType", "string")
            if storage not in ("string", "int"):
                raise InvalidProjectError(
                    f"Tiled project file '{file}': enum '{name}' has unknown "
                    f"storageType '{storage}'"
                )
            raw_values = entry.get("values", [])
            if not isinstance(raw_values, list) or not all(
                isinstance(value, str) for value in raw_values
            ):
                raise InvalidProjectError(
                    f"Tiled project file '{file}': enum '{name}' must define "
                    f"its values as a list of strings"
                )
            project.enums[name] = EnumType(
                name=name,
                storage_type=storage,
                values=list(raw_values),
                values_as_flags=bool(entry.get("valuesAsFlags", False)),
            )
        elif type_name == "class":
            if name in project.enums or name in project.classes:
                raise InvalidProjectError(
                    f"Tiled project file '{file}' defines duplicate type "
                    f"name '{name}'"
                )
            raw_members = entry.get("members", [])
            if not isinstance(raw_members, list):
                raise InvalidProjectError(
                    f"Tiled project file '{file}': class '{name}' members must "
                    f"be a list"
                )

            color = None
            raw_color = entry.get("color")
            if isinstance(raw_color, str) and raw_color:
                try:
                    color = parse_color(raw_color)
                except ValueError:
                    raise InvalidProjectError(
                        f"Tiled project file '{file}': class '{name}' has an "
                        f"invalid color '{raw_color}'"
                    )

            use_as = entry.get("useAs", [])
            if not isinstance(use_as, list):
                raise InvalidProjectError(
                    f"Tiled project file '{file}': class '{name}' has an "
                    f"invalid 'useAs' section"
                )

            project.classes[name] = ClassType(
                name=name, color=color, use_as=list(use_as)
            )
            pending_classes[name] = raw_members
        else:
            raise InvalidProjectError(
                f"Tiled project file '{file}' contains a type definition "
                f"with unknown type '{type_name}' (name '{name}')"
            )

    # Second pass: build class member records, validating that referenced
    # custom types exist.
    for class_name, raw_members in pending_classes.items():
        class_type = project.classes[class_name]
        for raw_member in raw_members:
            if not isinstance(raw_member, dict) or "name" not in raw_member:
                raise InvalidProjectError(
                    f"Tiled project file '{file}': class '{class_name}' has a "
                    f"malformed member definition: {raw_member!r}"
                )
            member_name = raw_member["name"]
            member_type = raw_member.get("type", "string")
            property_type = raw_member.get("propertyType")

            if member_type == "class":
                if property_type not in project.classes:
                    raise InvalidProjectError(
                        f"Tiled project file '{file}': member '{member_name}' "
                        f"of class '{class_name}' references unknown class "
                        f"'{property_type}'"
                    )
            elif member_type == "enum":
                if property_type not in project.enums:
                    raise InvalidProjectError(
                        f"Tiled project file '{file}': member '{member_name}' "
                        f"of class '{class_name}' references unknown enum "
                        f"'{property_type}'"
                    )
            elif member_type not in _SCALAR_TYPES:
                raise InvalidProjectError(
                    f"Tiled project file '{file}': member '{member_name}' of "
                    f"class '{class_name}' has unknown type '{member_type}'"
                )

            if member_name in class_type.members:
                raise InvalidProjectError(
                    f"Tiled project file '{file}': class '{class_name}' "
                    f"defines duplicate member '{member_name}'"
                )

            class_type.members[member_name] = ClassMember(
                name=member_name,
                type=member_type,
                property_type=property_type,
                default=raw_member.get("value"),
            )

    # Third pass: validate that class references are acyclic, then resolve
    # member default values now that every type is known.
    _check_class_cycles(project)
    for class_type in project.classes.values():
        for member in class_type.members.values():
            member.default = _parse_member_default(member, project, class_type.name)

    return project


def discover_project(directory: Path) -> Optional[Path]:
    """Find a single ``.tiled-project`` file inside ``directory``.

    Returns:
        The path to the project file, or ``None`` when the directory holds no
        project file.

    Raises:
        InvalidProjectError: When the directory contains multiple project
            files, since there is no way to know which one should be used.
    """
    directory = Path(directory)
    candidates = sorted(directory.glob("*.tiled-project"))
    if not candidates:
        return None
    if len(candidates) > 1:
        names = ", ".join(str(candidate) for candidate in candidates)
        raise InvalidProjectError(
            f"Ambiguous Tiled project: multiple project files found in "
            f"'{directory}': {names}"
        )
    return candidates[0]


def _apply_to_properties_holder(
    holder: Any, location: str, project: "Project", base_dir: Path
) -> None:
    """Apply class defaults to any parsed object carrying ``class_``."""
    class_name = getattr(holder, "class_", None)
    if class_name:
        holder.properties = effective_properties(
            project, class_name, holder.properties, location, base_dir=base_dir
        )


def _walk_layer(
    layer: Any, map_location: str, project: "Project", base_dir: Path
) -> None:
    location = f"layer '{layer.name}'"
    if getattr(layer, "id", None) is not None:
        location = f"{location} (id={layer.id})"
    location = f"{map_location}, {location}"

    _apply_to_properties_holder(layer, location, project, base_dir)

    # Object layers, including tile collision layers.
    tiled_objects = getattr(layer, "tiled_objects", None)
    if tiled_objects is not None:
        for tiled_object in tiled_objects:
            object_location = (
                f"{map_location}, object id={tiled_object.id} "
                f"('{tiled_object.name}') in layer '{layer.name}'"
            )
            _apply_to_properties_holder(
                tiled_object, object_location, project, base_dir
            )

    # Group layers contain further layers.
    child_layers = getattr(layer, "layers", None)
    if child_layers is not None:
        for child in child_layers:
            _walk_layer(child, map_location, project, base_dir)


def _walk_tileset(tileset: Any, project: "Project", base_dir: Path) -> None:
    tileset_location = f"tileset '{tileset.name}'"
    _apply_to_properties_holder(tileset, tileset_location, project, base_dir)

    for tile in (tileset.tiles or {}).values():
        tile_location = f"tile id={tile.id} in tileset '{tileset.name}'"
        _apply_to_properties_holder(tile, tile_location, project, base_dir)

        # Tiles may carry a collision object layer with its own objects.
        collision_layer = getattr(tile, "objects", None)
        if collision_layer is not None:
            _walk_layer(collision_layer, tile_location, project, base_dir)


def apply_project(
    target: Any, project: "Project", base_dir: Optional[Path] = None
) -> None:
    """Apply a project's class defaults to a parsed map or tileset in place.

    Every map, layer, object, tile and tileset that references a custom class
    gets its ``properties`` replaced by its effective properties. Nested class
    members are merged recursively, explicit values override defaults, and all
    values are validated against their declared types.

    Relative ``file`` property values explicitly stored in a map resolve
    against that map's directory; values in a standalone tileset resolve
    against the tileset's directory. Values authored as class member defaults
    in the project always resolve against the project directory.

    Args:
        target: A parsed :class:`~pytiled_parser.tiled_map.TiledMap` or
            :class:`~pytiled_parser.tileset.Tileset`.
        project: The project providing custom class and enum definitions.
        base_dir: Directory for resolving relative ``file`` values in a
            standalone tileset. Ignored for maps, which use the map file's
            own directory.
    """
    # Local import to avoid a circular import at module load time.
    from .tileset import Tileset

    if isinstance(target, Tileset):
        tileset_base_dir = (
            Path(base_dir) if base_dir is not None else Path.cwd()
        )
        _walk_tileset(target, project, tileset_base_dir)
        return

    base_dir = Path(target.map_file).parent
    map_location = f"map '{target.map_file}'"
    _apply_to_properties_holder(target, map_location, project, base_dir)

    for layer in target.layers:
        _walk_layer(layer, map_location, project, base_dir)

    for tileset in target.tilesets.values():
        _walk_tileset(tileset, project, base_dir)
