"""Resolution of `object` type custom properties.

Tiled allows custom properties of type `object`, whose value is the ID of
another object within the same map. After parsing, these properties are
[ObjectRef][pytiled_parser.properties.ObjectRef] instances which only hold
the raw ID value. This module provides
[resolve_object_references][pytiled_parser.object_ref.resolve_object_references]
to map those IDs to the actual [TiledObject][pytiled_parser.tiled_object.TiledObject]
instances they reference.

References are resolved across the whole map: object IDs are looked up in
every object layer (including layers nested inside group layers), so a
property on the map, a layer, an object, a tileset, or a tile can point to
an object living in any object layer. Objects created from object templates
are normal objects in their layer by the time resolution happens, so
references to and from template instances resolve like any other object,
with the explicitly overridden properties from the instance in place.

The referenced objects returned are the very same instances contained in the
map's layers, they are never copied.
"""

from typing import Dict, Iterator, List, Optional, Set, Tuple

import attr

from pytiled_parser.layer import Layer, LayerGroup, ObjectLayer
from pytiled_parser.properties import ClassProperty, ObjectRef, Properties
from pytiled_parser.tiled_map import TiledMap
from pytiled_parser.tiled_object import TiledObject


@attr.s(auto_attribs=True)
class ObjectRefError:
    """A problem encountered while resolving one `object` type property.

    Attributes:
        path: Path to the property within the map, for example
            ``layers[1].tiled_objects[3].properties['target']`` or
            ``tilesets[1].properties['target']``.
        message: Human readable description of the problem.
    """

    path: str
    message: str


@attr.s(auto_attribs=True)
class ObjectRefResolution:
    """The result of resolving the `object` type properties of a map.

    Attributes:
        resolved: Mapping of property path to the referenced TiledObject.
            The objects are the same instances contained in the map's layers,
            they are not copies.
        errors: Mapping of property path to the error encountered while
            resolving that property. A path never appears in both `resolved`
            and `errors`.
    """

    resolved: Dict[str, TiledObject] = attr.Factory(dict)
    errors: Dict[str, ObjectRefError] = attr.Factory(dict)


# A reference waiting to be resolved: (property path, reference, owning object)
# The owning object is the TiledObject the property belongs to, or None when
# the property belongs to the map, a layer, a tileset, or a tile.
_RefEntry = Tuple[str, ObjectRef, Optional[TiledObject]]

# A resolved reference between two objects: (property path, owner, target)
_RefEdge = Tuple[str, TiledObject, TiledObject]


def _iter_layers(
    layers: Optional[List[Layer]], prefix: str = ""
) -> Iterator[Tuple[str, Layer]]:
    """Yield (path, layer) for every layer, descending into layer groups."""
    for index, layer in enumerate(layers or []):
        path = f"{prefix}layers[{index}]"
        yield path, layer
        if isinstance(layer, LayerGroup):
            yield from _iter_layers(layer.layers, f"{path}.")


def _collect_from_properties(
    properties: Optional[Properties],
    base_path: str,
    owner: Optional[TiledObject],
    entries: List[_RefEntry],
) -> None:
    """Collect ObjectRef values from a property dict, recursing into classes."""
    if not properties:
        return
    for name, value in properties.items():
        path = f"{base_path}[{name!r}]"
        if isinstance(value, ObjectRef):
            entries.append((path, value, owner))
        elif isinstance(value, ClassProperty):
            _collect_from_properties(value, path, owner, entries)


def _flag_circular_references(
    edges: List[_RefEdge], resolution: ObjectRefResolution
) -> None:
    """Move edges that are part of a reference cycle from resolved to errors."""
    adjacency: Dict[int, List[TiledObject]] = {}
    for _, owner, target in edges:
        adjacency.setdefault(id(owner), []).append(target)

    def _chain_to(start: TiledObject, goal: TiledObject) -> Optional[List[TiledObject]]:
        """Breadth-first search for a chain of references from start to goal."""
        if id(start) == id(goal):
            return [start]
        visited = {id(start)}
        queue: List[Tuple[TiledObject, List[TiledObject]]] = [(start, [start])]
        while queue:
            node, chain = queue.pop(0)
            for next_object in adjacency.get(id(node), []):
                if id(next_object) == id(goal):
                    return chain + [next_object]
                if id(next_object) not in visited:
                    visited.add(id(next_object))
                    queue.append((next_object, chain + [next_object]))
        return None

    for path, owner, target in edges:
        if path not in resolution.resolved:
            continue
        chain = _chain_to(target, owner)
        if chain is not None:
            del resolution.resolved[path]
            cycle = " -> ".join(
                str(tiled_object.id) for tiled_object in [owner] + chain
            )
            resolution.errors[path] = ObjectRefError(
                path, f"Circular object reference detected: {cycle}"
            )


def resolve_object_references(tiled_map: TiledMap) -> ObjectRefResolution:
    """Resolve the `object` type properties of a map to the referenced objects.

    Every [ObjectRef][pytiled_parser.properties.ObjectRef] property on the
    map itself, on layers (including layers nested in group layers), on
    objects, on tilesets, and on tiles is resolved against all objects in the
    map's object layers. The original property values are left untouched.

    Args:
        tiled_map: The map to resolve object references for.

    Returns:
        ObjectRefResolution: The resolved references and per-property errors.
            Errors are reported (keyed by property path) for references to
            object IDs that do not exist in the map, IDs that exist more than
            once, ID 0, values that are not valid object IDs, and references
            that form a cycle.
    """
    index: Dict[int, TiledObject] = {}
    duplicates: Set[int] = set()
    entries: List[_RefEntry] = []

    layers = list(_iter_layers(tiled_map.layers))

    # Index every object in the map by ID, across all object layers.
    for _, layer in layers:
        if isinstance(layer, ObjectLayer):
            for tiled_object in layer.tiled_objects:
                if tiled_object.id in index:
                    duplicates.add(tiled_object.id)
                else:
                    index[tiled_object.id] = tiled_object

    # Collect every object reference property in the map.
    _collect_from_properties(tiled_map.properties, "properties", None, entries)

    for layer_path, layer in layers:
        _collect_from_properties(
            layer.properties, f"{layer_path}.properties", None, entries
        )
        if isinstance(layer, ObjectLayer):
            for object_index, tiled_object in enumerate(layer.tiled_objects):
                _collect_from_properties(
                    tiled_object.properties,
                    f"{layer_path}.tiled_objects[{object_index}].properties",
                    tiled_object,
                    entries,
                )

    for firstgid, tileset in tiled_map.tilesets.items():
        _collect_from_properties(
            tileset.properties, f"tilesets[{firstgid}].properties", None, entries
        )
        for tile_id, tile in (tileset.tiles or {}).items():
            _collect_from_properties(
                tile.properties,
                f"tilesets[{firstgid}].tiles[{tile_id}].properties",
                None,
                entries,
            )

    resolution = ObjectRefResolution()
    edges: List[_RefEdge] = []

    for path, ref, owner in entries:
        ref_id = ref.id
        if ref_id is None:
            resolution.errors[path] = ObjectRefError(
                path,
                f"Object reference has a value that is not a valid object ID: "
                f"{ref.value!r}",
            )
        elif ref_id == 0:
            resolution.errors[path] = ObjectRefError(
                path, "Object reference ID 0 does not point to any object"
            )
        elif ref_id in duplicates:
            resolution.errors[path] = ObjectRefError(
                path,
                f"Object reference ID {ref_id} matches multiple objects in the map",
            )
        elif ref_id not in index:
            resolution.errors[path] = ObjectRefError(
                path, f"No object with ID {ref_id} exists in the map"
            )
        else:
            target = index[ref_id]
            resolution.resolved[path] = target
            if owner is not None:
                edges.append((path, owner, target))

    _flag_circular_references(edges, resolution)

    return resolution
