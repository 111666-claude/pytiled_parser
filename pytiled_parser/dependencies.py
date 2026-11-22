"""Collect every external file a Tiled map depends on.

Parsing a map only reads the map file itself, the external tilesets and
templates it references, and nothing else. Images are stored as paths and
file properties are stored as paths, so a map can parse successfully while
still referencing files that need to be shipped with it. Packaging tools
need to know about all of those files.

[collect_dependencies][pytiled_parser.dependencies.collect_dependencies]
walks a map, its external tilesets and its object templates, and returns a
[DependencyManifest][pytiled_parser.dependencies.DependencyManifest] listing
every referenced file exactly once. Each
[MapDependency][pytiled_parser.dependencies.MapDependency] records the
canonical path of the file, its
[resource type][pytiled_parser.dependencies.ResourceType] and every
[DependencySource][pytiled_parser.dependencies.DependencySource] that
referenced it.

Relative paths are always resolved against the directory of the file that
contains the reference, exactly like the parser does: an image inside an
external tileset is relative to that tileset file, an image inside an
embedded tileset is relative to the map file, and a tileset referenced by
an object template is relative to the template file.

A file referenced more than once is listed once. Its ``sources`` list keeps
one entry per reference, so the same image used by two tilesets and an image
layer can be traced back to all three places. Files are walked depth first:
a tileset or template is walked completely as soon as it is referenced, and
within a file references are visited in the order they appear. Map level
references are visited in a fixed order so the manifest does not depend on
the order of the keys in a JSON file: external tilesets, then the map's own
properties, then the layers. Structural references (tilesets and templates)
are followed recursively. A file already on the current chain is reported as
a [cycle][pytiled_parser.dependencies.IssueCode.CYCLE] instead of being
followed again.

Nothing in this module modifies maps, tilesets or the result of
[parse_map][pytiled_parser.parse_map]. Dependencies are read straight from
the files, so a manifest can be built without parsing the map.

The parser supports references written in both the JSON and the TMX format,
so both are understood here, regardless of the format of the file that
contains them.
"""

import json
import os
import xml.etree.ElementTree as etree
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Union

import attr

from pytiled_parser.exception import UnknownFormat
from pytiled_parser.tiled_map import TiledMap
from pytiled_parser.util import check_format

# Layer elements, in the TMX sense. Only these can contain references.
_LAYER_TAGS = frozenset(("layer", "objectgroup", "imagelayer", "group"))


class ResourceType:
    """The values used by ``MapDependency.resource_type``.

    Each dependency and each of its sources records how the file was
    referenced. A file that is referenced in different ways keeps the type
    of the first reference on the dependency itself, while every source
    records its own type.
    """

    #: An external tileset file (TSX or JSON).
    TILESET = "tileset"
    #: An object template file (TX or JSON).
    TEMPLATE = "template"
    #: The ``image`` of a tileset (a sprite sheet).
    TILESET_IMAGE = "tileset_image"
    #: The ``image`` of an individual tile in an image collection tileset.
    TILE_IMAGE = "tile_image"
    #: The ``image`` of an image layer.
    IMAGE_LAYER_IMAGE = "image_layer_image"
    #: A custom property of type ``file``.
    FILE_PROPERTY = "file_property"


class IssueCode:
    """The values used by ``DependencyIssue.code``."""

    #: The referenced file does not exist or is not a file.
    MISSING = "missing"
    #: The referenced file resolves outside of the manifest root.
    OUTSIDE_ROOT = "outside_root"
    #: The reference could not be interpreted, the file could not be read,
    #: or the file does not have the structure expected of it.
    UNSUPPORTED = "unsupported"
    #: The reference is part of a cycle of tilesets and/or templates.
    CYCLE = "cycle"


@attr.s(auto_attribs=True, frozen=True)
class DependencySource:
    """One place in one file that references a dependency.

    Attributes:
        file: Canonical path of the file containing the reference. This is
            the file the relative path is resolved against.
        location: The location of the reference inside ``file``, for example
            ``layers[1].layers[0].objects[2].template``,
            ``tilesets[0].image`` or ``properties['config']``.
        reference: The reference exactly as it was written in the file.
        resource_type: How the file is referenced at this location. One of
            the values of
            [ResourceType][pytiled_parser.dependencies.ResourceType].
    """

    file: Path
    location: str
    reference: str
    resource_type: str


@attr.s(auto_attribs=True)
class MapDependency:
    """A single file referenced by a map.

    Attributes:
        path: Canonical, absolute path of the referenced file.
        resource_type: The resource type of the first reference encountered,
            one of the values of
            [ResourceType][pytiled_parser.dependencies.ResourceType]. When
            the same file is referenced as different kinds of resources, the
            individual types are available on each source.
        sources: Every reference to this file, in discovery order. A file
            referenced once has a single source.
        exists: Whether the file exists, when its existence was checked.
            ``None`` when existence was not checked, which is the case for
            images and file properties unless ``check_exists`` is set.
            Structural files (tilesets and templates) are always checked
            because they have to be read in order to be walked.
    """

    path: Path
    resource_type: str
    sources: List[DependencySource]
    exists: Optional[bool] = None


@attr.s(auto_attribs=True)
class DependencyIssue:
    """A problem encountered while collecting dependencies.

    Issues are reported instead of raised so a manifest can still be built
    for a map with broken references. Parsing the same map may still fail.

    Attributes:
        code: One of the values of
            [IssueCode][pytiled_parser.dependencies.IssueCode].
        path: Canonical path of the problematic file, when one could be
            determined. ``None`` for references whose value was not a usable
            path, for example a file property whose value is not a string.
        message: Human readable description of the problem, including the
            file and location of the reference that caused it.
        source: The reference that caused the issue, when it is known.
    """

    code: str
    path: Optional[Path]
    message: str
    source: Optional[DependencySource] = None


@attr.s(auto_attribs=True)
class DependencyManifest:
    """All files a map depends on, plus any issues found while collecting them.

    Attributes:
        map_file: Canonical path of the map the manifest was built for.
        dependencies: Every referenced file, in first-discovery order. A file
            referenced more than once appears once, with all of its sources.
        issues: Problems such as missing files, references outside of the
            root, unsupported references and cycles, in discovery order.
    """

    map_file: Path
    dependencies: List[MapDependency] = attr.Factory(list)
    issues: List[DependencyIssue] = attr.Factory(list)

    @property
    def paths(self) -> List[Path]:
        """List[Path]: The canonical paths of all dependencies, in order."""
        return [dependency.path for dependency in self.dependencies]

    @property
    def missing(self) -> List[Path]:
        """List[Path]: Paths of checked dependencies that do not exist."""
        return [
            dependency.path
            for dependency in self.dependencies
            if dependency.exists is False
        ]

    def has_issues(self, code: Optional[str] = None) -> bool:
        """Return whether any issue (of the given code) was found.

        Args:
            code: One of the values of
                [IssueCode][pytiled_parser.dependencies.IssueCode], or
                ``None`` to consider every issue.
        """
        if code is None:
            return bool(self.issues)
        return any(issue.code == code for issue in self.issues)


def _canonical(path: Path) -> Path:
    """Return a normalized, absolute path with symlinks resolved."""
    return Path(os.path.realpath(str(path)))


def _is_within(path: Path, root: Path) -> bool:
    """Return whether ``path`` is ``root`` or below it."""
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


class _Collector:
    """Walks a map and its referenced files, accumulating a manifest."""

    def __init__(
        self,
        map_file: Path,
        encoding: str,
        check_exists: bool,
        root: Path,
    ) -> None:
        self.encoding = encoding
        self.check_exists = check_exists
        self.root = root
        self.manifest = DependencyManifest(map_file=map_file)
        self._index: Dict[Path, MapDependency] = {}
        self._visited: Set[Path] = set()
        self._stack: List[Path] = []

    # -- recording ---------------------------------------------------------

    def _issue(
        self,
        code: str,
        path: Optional[Path],
        message: str,
        source: Optional[DependencySource] = None,
    ) -> None:
        self.manifest.issues.append(
            DependencyIssue(code=code, path=path, message=message, source=source)
        )

    def _record(self, path: Path, resource_type: str, source: DependencySource) -> None:
        dependency = self._index.get(path)
        if dependency is None:
            dependency = MapDependency(
                path=path, resource_type=resource_type, sources=[]
            )
            self._index[path] = dependency
            self.manifest.dependencies.append(dependency)
        dependency.sources.append(source)

    def _confirm_exists(self, path: Path, source: DependencySource) -> bool:
        """Record whether ``path`` exists, reporting it when it does not."""
        exists = path.is_file()
        self._index[path].exists = exists
        if not exists:
            self._issue(
                IssueCode.MISSING,
                path,
                f"File referenced at {source.location} in {source.file} does not exist or is not a file: "
                f"{path}",
                source,
            )
        return exists

    def _add(
        self,
        reference: Any,
        base_file: Path,
        location: str,
        resource_type: str,
        follow: Optional[str] = None,
    ) -> None:
        """Record one reference, optionally following it as a resource file.

        Args:
            reference: The raw value written in the file.
            base_file: The file containing the reference. Relative paths are
                resolved against its directory.
            location: Location of the reference within ``base_file``.
            resource_type: The resource type of the reference.
            follow: ``ResourceType.TILESET`` or ``ResourceType.TEMPLATE``
                when the referenced file itself contains further references
                and should be walked.
        """
        source = DependencySource(
            file=_canonical(base_file),
            location=location,
            reference=reference if isinstance(reference, str) else repr(reference),
            resource_type=resource_type,
        )

        if not isinstance(reference, str) or not reference.strip():
            self._issue(
                IssueCode.UNSUPPORTED,
                None,
                f"Reference at {location} in {source.file} is not a usable file path: {reference!r}",
                source,
            )
            return

        path = _canonical(base_file.parent / reference)
        self._record(path, resource_type, source)

        if not _is_within(path, self.root):
            self._issue(
                IssueCode.OUTSIDE_ROOT,
                path,
                f"Reference at {location} in {source.file} resolves to {path}, which is outside of the "
                f"manifest root {self.root}",
                source,
            )

        if follow is not None:
            self._follow(path, follow, source)
        elif self.check_exists:
            self._confirm_exists(path, source)

    def _follow(self, path: Path, kind: str, source: DependencySource) -> None:
        """Walk a tileset or template file, guarding against cycles."""
        if path in self._stack:
            start = self._stack.index(path)
            chain = self._stack[start:] + [path]
            self._issue(
                IssueCode.CYCLE,
                path,
                f"Circular {kind} reference detected: "
                + " -> ".join(str(item) for item in chain),
                source,
            )
            return

        # Structural files always have their existence checked; a missing one
        # is reported for each reference to it, like a missing leaf resource.
        if not self._confirm_exists(path, source):
            self._visited.add(path)
            return

        if path in self._visited:
            return

        try:
            file_format, raw = self._read(path)
        except (OSError, ValueError, IndexError, etree.ParseError) as exception:
            self._issue(
                IssueCode.UNSUPPORTED,
                path,
                f"Could not read {kind} referenced at {source.location} in {source.file}: {exception}",
                source,
            )
            self._visited.add(path)
            return

        self._stack.append(path)
        try:
            if kind == ResourceType.TILESET:
                if file_format == "tmx":
                    self._scan_tileset_tmx(raw, path, "tileset")
                else:
                    self._scan_tileset_json(raw, path, "tileset")
            else:
                self._scan_template(raw, file_format, path, source)
        except (
            AttributeError,
            IndexError,
            KeyError,
            TypeError,
            ValueError,
            etree.ParseError,
        ) as exception:
            self._issue(
                IssueCode.UNSUPPORTED,
                path,
                f"Could not process {kind} referenced at {source.location} in {source.file}: {exception}",
                source,
            )
        finally:
            self._stack.pop()
            self._visited.add(path)

    def _read(self, path: Path) -> Any:
        file_format = check_format(path, self.encoding)
        with open(path, encoding=self.encoding) as referenced_file:
            if file_format == "tmx":
                return file_format, etree.parse(referenced_file).getroot()
            return file_format, json.load(referenced_file)

    # -- map entry point ---------------------------------------------------

    def _scan_map(self) -> None:
        try:
            file_format, raw = self._read(self.manifest.map_file)
        except (ValueError, etree.ParseError) as exception:
            raise UnknownFormat(
                "Unknown Map Format, please use either the TMX or JSON format. "
                "This message could also mean your map file is invalid or corrupted."
            ) from exception
        map_path = self.manifest.map_file

        if file_format == "json" and not isinstance(raw, dict):
            raise UnknownFormat(
                "Unknown Map Format, the JSON map must be an object, not "
                f"{type(raw).__name__}."
            )

        # Map level references are always walked in this order: external
        # tilesets, then the map's own properties, then the layers. This
        # keeps the manifest order independent of how a JSON file happens to
        # order its keys.
        if file_format == "tmx":
            tileset_elements = raw.findall("./tileset")
            for index, element in enumerate(tileset_elements):
                self._scan_tileset_reference_tmx(
                    element, map_path, f"tilesets[{index}]"
                )

            properties_element = raw.find("./properties")
            if properties_element is not None:
                self._scan_properties_tmx(properties_element, map_path, "properties")

            layer_elements = [
                element for element in raw.findall("./*") if element.tag in _LAYER_TAGS
            ]
            for index, element in enumerate(layer_elements):
                self._scan_layer_tmx(element, map_path, f"layers[{index}]")
        else:
            raw_tilesets = raw.get("tilesets") or []
            for index, raw_tileset in enumerate(raw_tilesets):
                location = f"tilesets[{index}]"
                try:
                    self._scan_tileset_reference_json(raw_tileset, map_path, location)
                except (IndexError, KeyError, TypeError, ValueError) as exception:
                    self._issue(
                        IssueCode.UNSUPPORTED,
                        None,
                        f"Could not process tileset at {location} in {map_path}: "
                        f"{exception}",
                    )

            try:
                self._scan_properties_json(
                    raw.get("properties"), map_path, "properties"
                )
            except (IndexError, KeyError, TypeError, ValueError) as exception:
                self._issue(
                    IssueCode.UNSUPPORTED,
                    None,
                    f"Could not process properties in {map_path}: {exception}",
                )

            for index, raw_layer in enumerate(raw.get("layers") or []):
                location = f"layers[{index}]"
                try:
                    self._scan_layer_json(raw_layer, map_path, location)
                except (IndexError, KeyError, TypeError, ValueError) as exception:
                    self._issue(
                        IssueCode.UNSUPPORTED,
                        None,
                        f"Could not process layer at {location} in {map_path}: "
                        f"{exception}",
                    )

    # -- layers ------------------------------------------------------------

    def _scan_layer_json(self, raw: Any, base_file: Path, prefix: str) -> None:
        if not isinstance(raw, dict):
            raise TypeError(f"layer is not a JSON object: {raw!r}")

        for key, value in raw.items():
            if key == "properties":
                self._scan_properties_json(value, base_file, prefix + ".properties")
            elif key == "image":
                self._add(
                    value,
                    base_file,
                    prefix + ".image",
                    ResourceType.IMAGE_LAYER_IMAGE,
                )
            elif key == "objects":
                for index, raw_object in enumerate(value or []):
                    self._scan_object_json(
                        raw_object,
                        base_file,
                        f"{prefix}.objects[{index}]",
                    )
            elif key == "layers":
                for index, raw_child in enumerate(value or []):
                    self._scan_layer_json(
                        raw_child, base_file, f"{prefix}.layers[{index}]"
                    )

    def _scan_layer_tmx(self, element: Any, base_file: Path, prefix: str) -> None:
        for child in element:
            if child.tag == "properties":
                self._scan_properties_tmx(child, base_file, prefix + ".properties")
            elif child.tag == "image":
                self._add_tmx_image(
                    child,
                    base_file,
                    prefix + ".image",
                    ResourceType.IMAGE_LAYER_IMAGE,
                )
            elif child.tag == "object":
                self._scan_object_tmx(
                    child,
                    base_file,
                    f"{prefix}.objects["
                    f"{_child_index(element, child, frozenset(('object',)))}]",
                )
            elif child.tag in _LAYER_TAGS:
                self._scan_layer_tmx(
                    child,
                    base_file,
                    f"{prefix}.layers[{_child_index(element, child, _LAYER_TAGS)}]",
                )

    # -- objects -----------------------------------------------------------

    def _scan_object_json(self, raw: Any, base_file: Path, prefix: str) -> None:
        if not isinstance(raw, dict):
            raise TypeError(f"object is not a JSON object: {raw!r}")

        for key, value in raw.items():
            if key == "template":
                self._add(
                    value,
                    base_file,
                    prefix + ".template",
                    ResourceType.TEMPLATE,
                    follow=ResourceType.TEMPLATE,
                )
            elif key == "properties":
                self._scan_properties_json(value, base_file, prefix + ".properties")

    def _scan_object_tmx(self, element: Any, base_file: Path, prefix: str) -> None:
        template = element.attrib.get("template")
        if template is not None:
            self._add(
                template,
                base_file,
                prefix + ".template",
                ResourceType.TEMPLATE,
                follow=ResourceType.TEMPLATE,
            )

        properties_element = element.find("./properties")
        if properties_element is not None:
            self._scan_properties_tmx(
                properties_element, base_file, prefix + ".properties"
            )

    # -- tilesets ----------------------------------------------------------

    def _scan_tileset_reference_json(
        self, raw: Any, base_file: Path, prefix: str
    ) -> None:
        if not isinstance(raw, dict):
            raise TypeError(f"tileset is not a JSON object: {raw!r}")

        source = raw.get("source")
        if source is not None:
            self._add(
                source,
                base_file,
                prefix + ".source",
                ResourceType.TILESET,
                follow=ResourceType.TILESET,
            )
        else:
            self._scan_tileset_json(raw, base_file, prefix)

    def _scan_tileset_reference_tmx(
        self, element: Any, base_file: Path, prefix: str
    ) -> None:
        source = element.attrib.get("source")
        if source is not None:
            self._add(
                source,
                base_file,
                prefix + ".source",
                ResourceType.TILESET,
                follow=ResourceType.TILESET,
            )
        else:
            self._scan_tileset_tmx(element, base_file, prefix)

    def _scan_tileset_json(self, raw: Any, base_file: Path, prefix: str) -> None:
        if not isinstance(raw, dict):
            raise TypeError(f"tileset is not a JSON object: {raw!r}")

        for key, value in raw.items():
            if key == "properties":
                self._scan_properties_json(value, base_file, prefix + ".properties")
            elif key == "image":
                self._add(
                    value,
                    base_file,
                    prefix + ".image",
                    ResourceType.TILESET_IMAGE,
                )
            elif key == "tiles":
                self._scan_tiles_json(value, base_file, prefix)

    def _scan_tiles_json(self, raw_tiles: Any, base_file: Path, prefix: str) -> None:
        if isinstance(raw_tiles, dict):
            tile_items = list(raw_tiles.items())
        else:
            tile_items = list(enumerate(raw_tiles or []))

        for tile_key, raw_tile in tile_items:
            if not isinstance(raw_tile, dict):
                raise TypeError(f"tile is not a JSON object: {raw_tile!r}")
            tile_prefix = f"{prefix}.tiles[{raw_tile.get('id', tile_key)}]"
            self._scan_tile_json(raw_tile, base_file, tile_prefix)

    def _scan_tile_json(self, raw: Any, base_file: Path, prefix: str) -> None:
        for key, value in raw.items():
            if key == "properties":
                self._scan_properties_json(value, base_file, prefix + ".properties")
            elif key == "image":
                self._add(
                    value,
                    base_file,
                    prefix + ".image",
                    ResourceType.TILE_IMAGE,
                )
            elif key == "objectgroup":
                for index, raw_object in enumerate(value.get("objects") or []):
                    self._scan_object_json(
                        raw_object,
                        base_file,
                        f"{prefix}.objects[{index}]",
                    )

    def _scan_tileset_tmx(self, element: Any, base_file: Path, prefix: str) -> None:
        for child in element:
            if child.tag == "properties":
                self._scan_properties_tmx(child, base_file, prefix + ".properties")
            elif child.tag == "image":
                self._add_tmx_image(
                    child,
                    base_file,
                    prefix + ".image",
                    ResourceType.TILESET_IMAGE,
                )
            elif child.tag == "tile":
                tile_prefix = f"{prefix}.tiles[{child.attrib.get('id', '?')}]"
                self._scan_tile_tmx(child, base_file, tile_prefix)

    def _scan_tile_tmx(self, element: Any, base_file: Path, prefix: str) -> None:
        for child in element:
            if child.tag == "properties":
                self._scan_properties_tmx(child, base_file, prefix + ".properties")
            elif child.tag == "image":
                self._add_tmx_image(
                    child,
                    base_file,
                    prefix + ".image",
                    ResourceType.TILE_IMAGE,
                )
            elif child.tag == "objectgroup":
                for index, object_element in enumerate(child.findall("./object")):
                    self._scan_object_tmx(
                        object_element,
                        base_file,
                        f"{prefix}.objects[{index}]",
                    )

    # -- templates ---------------------------------------------------------

    def _scan_template(
        self,
        raw: Any,
        file_format: str,
        template_path: Path,
        source: DependencySource,
    ) -> None:
        if file_format == "tmx":
            self._scan_template_tmx(raw, template_path, source)
        else:
            self._scan_template_json(raw, template_path, source)

    def _scan_template_json(
        self, raw: Any, template_path: Path, source: DependencySource
    ) -> None:
        if not isinstance(raw, dict):
            raise TypeError(f"template is not a JSON object: {raw!r}")

        has_object = False
        for key, value in raw.items():
            if key == "tileset":
                if isinstance(value, dict) and value.get("source") is not None:
                    self._add(
                        value["source"],
                        template_path,
                        "tileset.source",
                        ResourceType.TILESET,
                        follow=ResourceType.TILESET,
                    )
                else:
                    self._scan_tileset_json(value, template_path, "tileset")
            elif key == "object":
                has_object = True
                self._scan_object_json(value, template_path, "object")

        if not has_object:
            self._issue(
                IssueCode.UNSUPPORTED,
                template_path,
                f"Template referenced at {source.location} in {source.file} does not contain an object: "
                f"{template_path}",
                source,
            )

    def _scan_template_tmx(
        self, raw: Any, template_path: Path, source: DependencySource
    ) -> None:
        has_object = False
        for child in raw:
            if child.tag == "tileset":
                tileset_source = child.attrib.get("source")
                if tileset_source is not None:
                    self._add(
                        tileset_source,
                        template_path,
                        "tileset.source",
                        ResourceType.TILESET,
                        follow=ResourceType.TILESET,
                    )
                else:
                    self._scan_tileset_tmx(child, template_path, "tileset")
            elif child.tag == "object":
                has_object = True
                self._scan_object_tmx(child, template_path, "object")

        if not has_object:
            self._issue(
                IssueCode.UNSUPPORTED,
                template_path,
                f"Template referenced at {source.location} in {source.file} does not contain an object: "
                f"{template_path}",
                source,
            )

    # -- properties --------------------------------------------------------

    def _scan_properties_json(
        self, raw_properties: Any, base_file: Path, prefix: str
    ) -> None:
        # Legacy JSON maps store properties as a plain name -> value dict.
        # Type information is lost in that form, so file properties can not
        # be identified.
        if not isinstance(raw_properties, list):
            return

        for raw_property in raw_properties:
            if not isinstance(raw_property, dict):
                raise TypeError(f"property is not a JSON object: {raw_property!r}")
            if raw_property.get("type") != "file":
                continue

            self._add(
                raw_property.get("value"),
                base_file,
                f"{prefix}[{raw_property.get('name', '')!r}]",
                ResourceType.FILE_PROPERTY,
            )

    def _scan_properties_tmx(self, element: Any, base_file: Path, prefix: str) -> None:
        for property_element in element.findall("./property"):
            if property_element.attrib.get("type") != "file":
                continue

            location = f"{prefix}[{property_element.attrib.get('name', '')!r}]"
            value = property_element.attrib.get("value", property_element.text)
            if value is None:
                self._issue(
                    IssueCode.UNSUPPORTED,
                    None,
                    f"File property at {location} in {_canonical(base_file)} "
                    "has no value",
                )
                continue

            self._add(
                value,
                base_file,
                location,
                ResourceType.FILE_PROPERTY,
            )

    # -- helpers -----------------------------------------------------------

    def _add_tmx_image(
        self, image_element: Any, base_file: Path, location: str, resource_type: str
    ) -> None:
        source = image_element.attrib.get("source")
        if source is None:
            self._issue(
                IssueCode.UNSUPPORTED,
                None,
                f"Image element at {location} in {_canonical(base_file)} has no source attribute",
            )
            return

        self._add(source, base_file, location, resource_type)


def _child_index(parent: Any, child: Any, kinds: frozenset) -> int:
    """Index of ``child`` among its siblings whose tag is in ``kinds``."""
    matches = [candidate for candidate in parent if candidate.tag in kinds]
    return matches.index(child)


def collect_dependencies(
    map_file: Union[Path, str, TiledMap],
    encoding: str = "utf-8",
    check_exists: bool = False,
    root: Optional[Union[Path, str]] = None,
) -> DependencyManifest:
    """Collect every external file referenced by a map.

    The map, its external tilesets and its object templates are walked
    recursively. Relative references are resolved against the directory of
    the file that contains them, so resources referenced by an external
    tileset or template are resolved correctly even when those files live in
    different directories. Both the JSON and the TMX format are understood
    for every kind of file.

    A file referenced multiple times is listed once, with every reference
    kept in
    [MapDependency.sources][pytiled_parser.dependencies.MapDependency].
    The inventory is ordered by first discovery, walking files depth first.
    Inside a file, references are visited in the order they appear, except
    for the map itself, whose references are always visited in this order:
    external tilesets, then the map's properties, then the layers.

    By default broken references are reported and the walk continues where
    possible. Missing leaf resources (images and file properties) are only
    checked when ``check_exists`` is true. Structural files (tilesets and
    templates) have to be read to be walked, so missing or unreadable ones
    are always reported. Cycles between tilesets and templates are detected
    and reported instead of being followed.

    The parsed objects produced by
    [parse_map][pytiled_parser.parse_map] are not modified, and the result of
    parsing is not needed: dependencies are collected from the files
    themselves.

    Args:
        map_file: Path to the map file, or a parsed
            [TiledMap][pytiled_parser.tiled_map.TiledMap] whose ``map_file``
            is set.
        encoding: The character encoding set to use when opening files.
        check_exists: When true, every referenced file is checked for
            existence and missing files are reported as
            [IssueCode.MISSING][pytiled_parser.dependencies.IssueCode.MISSING]
            issues. When false, only files that have to be read to walk the
            map are checked.
        root: Directory that dependencies are expected to live in. Defaults
            to the directory containing the map. References resolving outside
            of it are reported as
            [IssueCode.OUTSIDE_ROOT][pytiled_parser.dependencies.IssueCode.OUTSIDE_ROOT]
            issues but are still followed and listed.

    Returns:
        DependencyManifest: The ordered list of dependencies with all of
            their sources, plus any issues found.

    Raises:
        FileNotFoundError: If the map file itself does not exist.
        UnknownFormat: If the map file is not a valid Tiled map file.
        ValueError: If a parsed TiledMap without a ``map_file`` is passed.
    """
    if isinstance(map_file, TiledMap):
        if map_file.map_file is None:
            raise ValueError(
                "The TiledMap passed to collect_dependencies has no map_file. "
                "Pass the path to the map file instead."
            )
        path = Path(map_file.map_file)
    else:
        path = Path(map_file)

    map_path = _canonical(path)
    root_path = _canonical(Path(root) if root is not None else path.parent)

    collector = _Collector(
        map_file=map_path,
        encoding=encoding,
        check_exists=check_exists,
        root=root_path,
    )
    collector._scan_map()
    return collector.manifest


def check_dependencies(
    map_file: Union[Path, str, TiledMap],
    encoding: str = "utf-8",
    root: Optional[Union[Path, str]] = None,
) -> DependencyManifest:
    """Collect dependencies and verify that every referenced file exists.

    This is the same as
    [collect_dependencies][pytiled_parser.dependencies.collect_dependencies]
    with ``check_exists=True``.

    Args:
        map_file: Path to the map file, or a parsed TiledMap whose
            ``map_file`` is set.
        encoding: The character encoding set to use when opening files.
        root: Directory that dependencies are expected to live in. Defaults
            to the directory containing the map.

    Returns:
        DependencyManifest: The manifest, with missing files reported in
            ``issues`` and reflected in each dependency's ``exists`` value.
    """
    return collect_dependencies(
        map_file, encoding=encoding, check_exists=True, root=root
    )
