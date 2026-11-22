"""Stable difference reports between two parsed Tiled maps.

The module compares two :class:`~pytiled_parser.tiled_map.TiledMap` instances
that have already been parsed by pytiled-parser. It is intended for tools such
as a level editor previewer, which need to know *what changed* without
rebuilding the entire map.

Identity rules
--------------

* Layers are matched by their Tiled ``id``. Objects are matched by their
  object ``id``. Names are never used to match entries that have ids, so two
  layers with the same name but different ids are reported as one removal and
  one addition, never as one renamed layer.
* If a layer or object has no id (hand-written or very old files), it is
  matched by a deterministic structural path instead: the parent's structural
  path, the entry kind, the name and the ordinal among identically named
  siblings. Such matches are marked ``matched_by="path"`` and add a warning.
  A real id is never guessed or invented.
* Duplicate ids are illegal in Tiled files but can occur in hand-edited ones.
  They are paired in document order and reported in ``warnings``.

Tile identity
-------------

A tile cell is identified by its flip flags and by ``(tileset, local tile id)``,
not by the raw global id. If an external tileset's ``firstgid`` moves but a
cell still points at the same tile, the cell is *not* reported as changed; the
change is reported once on the tileset (``change="remapped"``). Likewise, a
change inside an external tileset is reported on the tileset, not smeared over
every cell that uses it. The four Tiled GID flag bits (horizontal, vertical,
diagonal, hex-120) are stripped before resolving the tileset and tile id and
are compared separately.

Infinite maps
-------------

Finite tile layers (``layer.data``) are addressed by ``(column, row)``.
Infinite tile layers (``layer.chunks``) are normalised to absolute map tile
coordinates (``chunk.coordinates + local``). When both sides are infinite,
chunks are compared at chunk level first:

* a chunk present on both sides with the same size has its cells compared
  individually;
* an added, removed or resized chunk is reported as a chunk change and its
  cells are *not* duplicated as individual cell changes.

If a layer changes between finite and chunked representation, cells are
compared directly and no chunk changes are emitted.

Incompatible maps
-----------------

No exception is raised for maps whose size, tile size, orientation or
``infinite`` flag differ. Those differences are reported as map-level changes,
and cells are compared per absolute tile coordinate. Consequently, growing a
map produces added cells only at the new coordinates; coordinates outside one
side's data are simply absent there. Tile identity is resolved through the
tileset, so a tile-size change alone does not hide cell changes.

The following map metadata is intentionally not part of the report, because it
does not affect a preview: ``map_file``, ``version``, ``tiled_version``,
``next_layer_id`` and ``next_object_id``. Tileset ``version`` and
``tiled_version`` are ignored for the same reason.

A layer that is added, removed or replaced is reported as a single entry; its
objects and cells are part of the layer payload and are not repeated one by
one. Likewise, objects and cells inside a removed group are not enumerated.
Changing a layer from one type to another (for example a tile layer becoming an
object layer) with the same id is reported as ``replaced``.

The report never mutates either input map. Every collection in it is sorted by
a stable key, so the same two inputs always produce an identical report.

Replay
------

:func:`apply_map_diff` applies a report to a deep copy of the old map and
returns the result. For maps produced by pytiled-parser this result is
equivalent to the new map according to :func:`diff_maps`; only the
intentionally ignored metadata above is left at its old value. A pure
``remapped`` tileset change rewrites raw GIDs in place (flags included) so no
cell changes are needed. Replay assumes that duplicate ids do not occur.
"""

import copy
from typing import Any, Dict, Iterable, Iterator, List, NamedTuple, Optional, Tuple

import attr

from pytiled_parser.layer import (
    Chunk,
    ImageLayer,
    Layer,
    LayerGroup,
    ObjectLayer,
    TileLayer,
)
from pytiled_parser.tiled_map import TiledMap
from pytiled_parser.tiled_object import Tile, TiledObject
from pytiled_parser.tileset import Tile as TilesetTile
from pytiled_parser.tileset import Tileset

# Change kinds used throughout the report.
ADDED = "added"
REMOVED = "removed"
MODIFIED = "modified"
MOVED = "moved"
REPLACED = "replaced"
REMAPPED = "remapped"
RESIZED = "resized"

# Tiled stores flip/rotation information in the high bits of a GID.
_FLIPPED_HORIZONTALLY_FLAG = 0x80000000
_FLIPPED_VERTICALLY_FLAG = 0x40000000
_FLIPPED_DIAGONALLY_FLAG = 0x20000000
_ROTATED_HEX_120_FLAG = 0x10000000
_GID_MASK = 0x0FFFFFFF

# Attributes compared for every layer, regardless of its type. Type-specific
# attributes are added by _layer_fields().
_COMMON_LAYER_FIELDS = (
    "name",
    "opacity",
    "visible",
    "repeat_x",
    "repeat_y",
    "coordinates",
    "parallax_factor",
    "offset",
    "class_",
    "size",
    "properties",
    "tint_color",
)

# Attributes that are handled structurally rather than as plain values.
_STRUCTURAL_OBJECT_FIELDS = {
    "id",
    "properties",
    "new_tileset",
    "new_tileset_path",
}

# Tileset attributes compared as plain values. ``firstgid`` is included here
# and a change to it alone is reported as ``remapped``; ``tiles`` are compared
# by :func:`_tile_definition_changes`.
_TILESET_FIELDS = (
    "name",
    "tile_width",
    "tile_height",
    "tile_count",
    "columns",
    "type",
    "tile_render_size",
    "fill_mode",
    "spacing",
    "margin",
    "image",
    "image_width",
    "image_height",
    "transformations",
    "class_",
    "background_color",
    "tile_offset",
    "transparent_color",
    "grid",
    "properties",
    "wang_sets",
    "alignment",
    "firstgid",
)

_MAP_FIELDS = (
    "infinite",
    "map_size",
    "orientation",
    "render_order",
    "tile_size",
    "background_color",
    "properties",
    "hex_side_length",
    "stagger_axis",
    "stagger_index",
    "class_",
    "parallax_origin",
)


class TileCoordinate(NamedTuple):
    """An absolute tile coordinate on a map."""

    x: int
    y: int


@attr.s(auto_attribs=True, frozen=True)
class TileRef:
    """Semantic reference to one tile in a cell.

    Attributes:
        raw_gid: The value exactly as stored in the map, including flag bits.
        gid: The value with all flip/rotation flag bits stripped.
        tileset: The name of the tileset the tile belongs to, if resolvable.
        tile_id: The local id within the tileset, if resolvable.
        flipped_horizontally: Whether the horizontal flip bit is set.
        flipped_vertically: Whether the vertical flip bit is set.
        flipped_diagonally: Whether the diagonal flip bit is set.
        rotated_hex_120: Whether the hex-120 rotation bit is set.
        tileset_token: Stable token used to compare references across maps.
            Two refs that point at the same matched tileset share a token. It
            is intentionally independent of ``raw_gid`` so a ``firstgid``
            remap alone does not look like a cell change.
    """

    raw_gid: int
    gid: int
    tileset: Optional[str]
    tile_id: Optional[int]
    flipped_horizontally: bool = False
    flipped_vertically: bool = False
    flipped_diagonally: bool = False
    rotated_hex_120: bool = False
    tileset_token: Optional[Tuple[Any, ...]] = None

    def semantic_key(self) -> Tuple[Any, ...]:
        """Return the identity used to compare two tile references."""
        return (
            self.tileset_token,
            self.tile_id,
            self.gid if self.tileset_token is None else None,
            self.flipped_horizontally,
            self.flipped_vertically,
            self.flipped_diagonally,
            self.rotated_hex_120,
        )


@attr.s(auto_attribs=True, frozen=True)
class TilesetRef:
    """Lightweight description of one side's tileset.

    Attributes:
        name: The tileset name.
        firstgid: The first global id assigned to this tileset on this map.
        image: The string form of the tileset image, or ``None``.
    """

    name: str
    firstgid: int
    image: Optional[str] = None


@attr.s(auto_attribs=True, frozen=True)
class MapChange:
    """A change to a map-level attribute.

    Attributes:
        field: The :class:`TiledMap` attribute name.
        before: Value in the old map.
        after: Value in the new map.
    """

    field: str
    before: Any
    after: Any


@attr.s(auto_attribs=True, frozen=True)
class PropertyChange:
    """A change to one property of an object.

    ``None`` means the property is absent on that side.
    """

    name: str
    change: str
    before: Any = None
    after: Any = None


@attr.s(auto_attribs=True, frozen=True)
class LayerChange:
    """A change to a layer.

    Attributes:
        layer_id: The stable Tiled layer id, or ``None`` if the file had none.
        layer_name: Layer name on the side described by ``path``.
        layer_type: Layer class name, e.g. ``"TileLayer"``.
        path: Stable id path from the map root to this layer, inclusive.
            ``None`` entries correspond to layers without an id.
        change: One of ``added``, ``removed``, ``modified``, ``moved`` or
            ``replaced``. ``moved`` covers both a change of parent group and a
            change of order among matched siblings. An insertion or removal of
            a sibling does not mark the remaining siblings as moved.
        field_changes: Mapping of attribute name to ``(before, after)`` for
            common layer fields (visibility, offset, opacity, size, ...).
        old_path: Previous id path when the layer moved or was removed.
        new_path: New id path when the layer moved or was added.
        old_index: Previous sibling index.
        new_index: New sibling index.
        old_parent_path: Previous parent id path (empty tuple for the root).
        new_parent_path: New parent id path.
        matched_by: ``"id"`` or ``"path"``.
    """

    layer_id: Optional[int]
    layer_name: str
    layer_type: str
    path: Tuple[Optional[int], ...]
    change: str
    field_changes: Dict[str, Tuple[Any, Any]] = attr.Factory(dict)
    old_path: Optional[Tuple[Optional[int], ...]] = None
    new_path: Optional[Tuple[Optional[int], ...]] = None
    old_index: Optional[int] = None
    new_index: Optional[int] = None
    old_parent_path: Optional[Tuple[Optional[int], ...]] = None
    new_parent_path: Optional[Tuple[Optional[int], ...]] = None
    matched_by: str = "id"
    old_layer: Optional[Layer] = attr.ib(default=None, eq=False, repr=False)
    new_layer: Optional[Layer] = attr.ib(default=None, eq=False, repr=False)


@attr.s(auto_attribs=True, frozen=True)
class ObjectChange:
    """A change to an object inside an object layer.

    Attributes:
        object_id: The stable Tiled object id, or ``None`` if it had none.
        object_type: Object class name, e.g. ``"Rectangle"`` or ``"Tile"``.
        layer_id: The object layer id on the side described by ``layer_path``.
        layer_path: Id path of the owning layer.
        change: One of ``added``, ``removed``, ``modified``, ``moved`` or
            ``replaced``. ``moved`` covers a change of layer and a change of
            order among matched siblings.
        field_changes: Mapping of attribute name to ``(before, after)``.
            Properties are reported separately in ``property_changes``. For
            tile objects the ``gid`` entries are :class:`TileRef` objects, so
            a tileset remap alone is not reported as a change.
        property_changes: Sorted property additions, removals and edits.
        old_layer_path: Previous layer id path when the object moved.
        new_layer_path: New layer id path when the object moved.
        old_index: Previous index within the old layer.
        new_index: New index within the new layer.
        matched_by: ``"id"`` or ``"path"``.
    """

    object_id: Optional[int]
    object_type: str
    layer_id: Optional[int]
    layer_path: Tuple[Optional[int], ...]
    change: str
    field_changes: Dict[str, Tuple[Any, Any]] = attr.Factory(dict)
    property_changes: Tuple[PropertyChange, ...] = ()
    old_layer_path: Optional[Tuple[Optional[int], ...]] = None
    new_layer_path: Optional[Tuple[Optional[int], ...]] = None
    old_index: Optional[int] = None
    new_index: Optional[int] = None
    matched_by: str = "id"
    old_object: Optional[TiledObject] = attr.ib(default=None, eq=False, repr=False)
    new_object: Optional[TiledObject] = attr.ib(default=None, eq=False, repr=False)
    old_layer: Optional[ObjectLayer] = attr.ib(default=None, eq=False, repr=False)
    new_layer: Optional[ObjectLayer] = attr.ib(default=None, eq=False, repr=False)


@attr.s(auto_attribs=True, frozen=True)
class CellChange:
    """A change to one tile cell.

    Attributes:
        layer_id: Id of the owning tile layer.
        layer_path: Id path of the owning tile layer.
        coordinate: Absolute tile coordinate.
        change: ``added``, ``removed`` or ``modified``.
        old: Old tile reference, or ``None`` for an added cell.
        new: New tile reference, or ``None`` for a removed cell.
        chunk_coordinate: For infinite layers, the chunk the cell belongs to.
    """

    layer_id: Optional[int]
    layer_path: Tuple[Optional[int], ...]
    coordinate: TileCoordinate
    change: str
    old: Optional[TileRef] = None
    new: Optional[TileRef] = None
    chunk_coordinate: Optional[TileCoordinate] = None
    layer: Optional[TileLayer] = attr.ib(default=None, eq=False, repr=False)


@attr.s(auto_attribs=True, frozen=True)
class ChunkChange:
    """A structural change to an infinite-layer chunk.

    Cells inside added, removed or resized chunks are not repeated in
    ``CellChange`` entries; this entry carries the whole chunk instead.
    """

    layer_id: Optional[int]
    layer_path: Tuple[Optional[int], ...]
    coordinate: TileCoordinate
    change: str
    old_size: Optional[Tuple[float, float]] = None
    new_size: Optional[Tuple[float, float]] = None
    chunk: Optional[Chunk] = attr.ib(default=None, eq=False, repr=False)
    layer: Optional[TileLayer] = attr.ib(default=None, eq=False, repr=False)


@attr.s(auto_attribs=True, frozen=True)
class TileDefinitionChange:
    """A change to a tile definition inside a tileset."""

    tile_id: int
    change: str
    field_changes: Dict[str, Tuple[Any, Any]] = attr.Factory(dict)


@attr.s(auto_attribs=True, frozen=True)
class TilesetChange:
    """A change to a tileset.

    Attributes:
        old: Reference for the old side, or ``None`` for an added tileset.
        new: Reference for the new side, or ``None`` for a removed tileset.
        change: ``added``, ``removed``, ``modified`` or ``remapped``.
            ``remapped`` means the only change is the ``firstgid``; the same
            tiles are now referenced through a different global id range.
        matched_by: ``"identity"`` (name and image), ``"name"`` or
            ``"firstgid"``. ``"name"`` covers external tilesets whose image
            moved; ``"firstgid"`` is a last-resort fallback.
        field_changes: Tileset attribute changes, including ``firstgid``.
        tile_changes: Per-tile-definition changes, sorted by local tile id.
    """

    old: Optional[TilesetRef]
    new: Optional[TilesetRef]
    change: str
    matched_by: str = "identity"
    field_changes: Dict[str, Tuple[Any, Any]] = attr.Factory(dict)
    tile_changes: Tuple[TileDefinitionChange, ...] = ()
    tileset: Optional[Tileset] = attr.ib(default=None, eq=False, repr=False)


@attr.s(auto_attribs=True, frozen=True)
class MapDiff:
    """The complete, deterministically ordered difference between two maps.

    Attributes:
        map_changes: Map-level attribute changes, sorted by field name.
        tileset_changes: Tileset changes, sorted by old/new firstgid.
        layer_changes: Layer changes in structural order (new order where the
            layer exists in the new map, old order for removals).
        object_changes: Object changes sorted by layer path, then index.
        cell_changes: Cell changes sorted by layer path, then y, then x.
        chunk_changes: Chunk changes sorted by layer path, then y, then x.
        warnings: Stable human-readable policy warnings (missing ids,
            duplicate ids, last-resort tileset matches), sorted.
    """

    map_changes: Tuple[MapChange, ...] = ()
    tileset_changes: Tuple[TilesetChange, ...] = ()
    layer_changes: Tuple[LayerChange, ...] = ()
    object_changes: Tuple[ObjectChange, ...] = ()
    cell_changes: Tuple[CellChange, ...] = ()
    chunk_changes: Tuple[ChunkChange, ...] = ()
    warnings: Tuple[str, ...] = ()

    @property
    def is_empty(self) -> bool:
        """Whether the report contains no changes (warnings do not count)."""
        return not (
            self.map_changes
            or self.tileset_changes
            or self.layer_changes
            or self.object_changes
            or self.cell_changes
            or self.chunk_changes
        )


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------


def _normalize(value: Any) -> Any:
    """Convert a parsed value to a deterministic, recursively comparable form."""

    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return tuple(_normalize(item) for item in value)
    if isinstance(value, dict):
        return tuple(
            sorted(
                ((repr(key), _normalize(item)) for key, item in value.items()),
                key=lambda item: item[0],
            )
        )
    if isinstance(value, (set, frozenset)):
        return tuple(sorted(repr(item) for item in value))
    if attr.has(type(value)):
        fields = []
        for field in attr.fields(type(value)):
            if field.name in ("tileset", "new_tileset", "new_tileset_path"):
                continue
            fields.append((field.name, _normalize(getattr(value, field.name))))
        return (type(value).__name__, tuple(fields))
    return (type(value).__name__, repr(value))


def _values_equal(left: Any, right: Any) -> bool:
    return _normalize(left) == _normalize(right)


def _diff_fields(
    old: Any,
    new: Any,
    field_names: Tuple[str, ...],
) -> Dict[str, Tuple[Any, Any]]:
    """Return ``{field: (old, new)}`` for fields whose values differ."""

    changes: Dict[str, Tuple[Any, Any]] = {}
    for field_name in field_names:
        old_value = getattr(old, field_name, None)
        new_value = getattr(new, field_name, None)
        if not _values_equal(old_value, new_value):
            changes[field_name] = (old_value, new_value)
    return changes


def _layer_fields(layer: Layer) -> Tuple[str, ...]:
    fields = list(_COMMON_LAYER_FIELDS)
    if isinstance(layer, ObjectLayer):
        fields.append("draw_order")
    elif isinstance(layer, ImageLayer):
        fields.extend(("image", "transparent_color"))
    return tuple(fields)


def _object_field_names(obj: TiledObject) -> Tuple[str, ...]:
    return tuple(
        field.name
        for field in attr.fields(type(obj))
        if field.name not in _STRUCTURAL_OBJECT_FIELDS
    )


def _property_changes(
    old_properties: Optional[Dict[str, Any]],
    new_properties: Optional[Dict[str, Any]],
) -> Tuple[PropertyChange, ...]:
    old_properties = old_properties or {}
    new_properties = new_properties or {}
    changes: List[PropertyChange] = []
    for name in sorted(set(old_properties) | set(new_properties)):
        old_present = name in old_properties
        new_present = name in new_properties
        if old_present and new_present:
            if not _values_equal(old_properties[name], new_properties[name]):
                changes.append(
                    PropertyChange(
                        name=name,
                        change=MODIFIED,
                        before=old_properties[name],
                        after=new_properties[name],
                    )
                )
        elif old_present:
            changes.append(
                PropertyChange(name=name, change=REMOVED, before=old_properties[name])
            )
        else:
            changes.append(
                PropertyChange(name=name, change=ADDED, after=new_properties[name])
            )
    return tuple(changes)


def _path_key(path: Tuple[Optional[int], ...]) -> Tuple[int, ...]:
    return tuple(-1 if value is None else value for value in path)


# ---------------------------------------------------------------------------
# Tileset matching and tile resolution
# ---------------------------------------------------------------------------


class _TilesetResolver:
    """Resolve a raw GID to a semantic :class:`TileRef` for one map side."""

    def __init__(
        self,
        tilesets: Dict[int, Tileset],
        tokens: Dict[int, Tuple[Any, ...]],
    ) -> None:
        self._tilesets = sorted(
            (
                (firstgid, tileset, tokens.get(id(tileset)))
                for firstgid, tileset in tilesets.items()
            ),
            key=lambda item: item[0],
            reverse=True,
        )

    def resolve(self, raw_gid: Optional[int]) -> Optional[TileRef]:
        if raw_gid is None:
            return None
        base_gid = raw_gid & _GID_MASK
        if base_gid == 0:
            return None

        tile = None
        for firstgid, tileset, token in self._tilesets:
            if base_gid >= firstgid:
                tile = (tileset.name, base_gid - firstgid, token)
                break

        return TileRef(
            raw_gid=raw_gid,
            gid=base_gid,
            tileset=tile[0] if tile else None,
            tile_id=tile[1] if tile else None,
            flipped_horizontally=bool(raw_gid & _FLIPPED_HORIZONTALLY_FLAG),
            flipped_vertically=bool(raw_gid & _FLIPPED_VERTICALLY_FLAG),
            flipped_diagonally=bool(raw_gid & _FLIPPED_DIAGONALLY_FLAG),
            rotated_hex_120=bool(raw_gid & _ROTATED_HEX_120_FLAG),
            tileset_token=tile[2] if tile else None,
        )


def _tileset_identity(tileset: Tileset) -> Tuple[Any, ...]:
    image = str(tileset.image) if tileset.image is not None else None
    return (tileset.name, image)


def _tileset_ref(tileset: Tileset) -> TilesetRef:
    return TilesetRef(
        name=tileset.name,
        firstgid=tileset.firstgid,
        image=str(tileset.image) if tileset.image is not None else None,
    )


def _tile_definition_changes(
    old_tiles: Optional[Dict[int, TilesetTile]],
    new_tiles: Optional[Dict[int, TilesetTile]],
) -> Tuple[TileDefinitionChange, ...]:
    old_tiles = old_tiles or {}
    new_tiles = new_tiles or {}
    changes: List[TileDefinitionChange] = []
    for tile_id in sorted(set(old_tiles) | set(new_tiles)):
        if tile_id not in old_tiles:
            changes.append(TileDefinitionChange(tile_id=tile_id, change=ADDED))
        elif tile_id not in new_tiles:
            changes.append(TileDefinitionChange(tile_id=tile_id, change=REMOVED))
        else:
            field_changes = _diff_fields(
                old_tiles[tile_id],
                new_tiles[tile_id],
                tuple(
                    field.name
                    for field in attr.fields(type(new_tiles[tile_id]))
                    if field.name != "tileset"
                ),
            )
            if field_changes:
                changes.append(
                    TileDefinitionChange(
                        tile_id=tile_id,
                        change=MODIFIED,
                        field_changes=field_changes,
                    )
                )
    return tuple(changes)


def _match_tilesets(old_map: TiledMap, new_map: TiledMap, warnings: List[str]) -> Tuple[
    List[TilesetChange],
    Dict[int, Tuple[Any, ...]],
    Dict[int, Tuple[Any, ...]],
]:
    """Match tilesets by identity, then name, then firstgid."""

    remaining_old = sorted(old_map.tilesets.items(), key=lambda item: item[0])
    remaining_new = sorted(new_map.tilesets.items(), key=lambda item: item[0])
    pairs: List[Tuple[int, Tileset, int, Tileset, str]] = []

    stages = (
        ("identity", _tileset_identity),
        ("name", lambda tileset: tileset.name),
        ("firstgid", lambda tileset: tileset.firstgid),
    )
    for stage_name, keyfunc in stages:
        old_groups: Dict[Any, List[Tuple[int, Tileset]]] = {}
        new_groups: Dict[Any, List[Tuple[int, Tileset]]] = {}
        for item in remaining_old:
            old_groups.setdefault(keyfunc(item[1]), []).append(item)
        for item in remaining_new:
            new_groups.setdefault(keyfunc(item[1]), []).append(item)

        used_old: set = set()
        used_new: set = set()
        for key in sorted(set(old_groups) & set(new_groups), key=repr):
            old_group = old_groups[key]
            new_group = new_groups[key]
            if stage_name != "identity" and (len(old_group) > 1 or len(new_group) > 1):
                warnings.append(
                    "duplicate tileset {} {}; paired in document order".format(
                        stage_name, key
                    )
                )
            for old_pair, new_pair in zip(old_group, new_group):
                pairs.append(
                    (
                        old_pair[0],
                        old_pair[1],
                        new_pair[0],
                        new_pair[1],
                        stage_name,
                    )
                )
                used_old.add(old_pair[0])
                used_new.add(new_pair[0])

        remaining_old = [item for item in remaining_old if item[0] not in used_old]
        remaining_new = [item for item in remaining_new if item[0] not in used_new]

    changes: List[TilesetChange] = []
    old_tokens: Dict[int, Tuple[Any, ...]] = {}
    new_tokens: Dict[int, Tuple[Any, ...]] = {}

    for occurrence, (old_first, old_ts, new_first, new_ts, matched_by) in enumerate(
        sorted(pairs, key=lambda pair: (pair[0], pair[2]))
    ):
        token = ("paired", _tileset_identity(new_ts), occurrence)
        old_tokens[id(old_ts)] = token
        new_tokens[id(new_ts)] = token
        if matched_by != "identity":
            warnings.append(
                "tileset {!r} matched by {} because its identity could not be "
                "confirmed".format(new_ts.name, matched_by)
            )

        field_changes = _diff_fields(old_ts, new_ts, _TILESET_FIELDS)
        tile_changes = _tile_definition_changes(old_ts.tiles, new_ts.tiles)
        if not field_changes and not tile_changes:
            continue

        if set(field_changes) == {"firstgid"} and not tile_changes:
            change = REMAPPED
        else:
            change = MODIFIED

        changes.append(
            TilesetChange(
                old=_tileset_ref(old_ts),
                new=_tileset_ref(new_ts),
                change=change,
                matched_by=matched_by,
                field_changes=field_changes,
                tile_changes=tile_changes,
                tileset=new_ts,
            )
        )

    for occurrence, (firstgid, tileset) in enumerate(remaining_old):
        old_tokens[id(tileset)] = (
            "removed",
            _tileset_identity(tileset),
            firstgid,
            occurrence,
        )
        changes.append(
            TilesetChange(
                old=_tileset_ref(tileset), new=None, change=REMOVED, tileset=tileset
            )
        )

    for occurrence, (firstgid, tileset) in enumerate(remaining_new):
        new_tokens[id(tileset)] = (
            "added",
            _tileset_identity(tileset),
            firstgid,
            occurrence,
        )
        changes.append(
            TilesetChange(
                old=None, new=_tileset_ref(tileset), change=ADDED, tileset=tileset
            )
        )

    changes.sort(
        key=lambda change: (
            -1 if change.old is None else change.old.firstgid,
            -1 if change.new is None else change.new.firstgid,
            change.change,
        )
    )
    return changes, old_tokens, new_tokens


# ---------------------------------------------------------------------------
# Layer traversal and matching
# ---------------------------------------------------------------------------


@attr.s(auto_attribs=True)
class _Node:
    """A layer together with its structural position on one map."""

    layer: Layer
    parent: Optional["_Node"]
    index: int
    path: Tuple[Optional[int], ...]
    parent_path: Tuple[Optional[int], ...]
    order: Tuple[int, ...]
    kind: str
    name: str
    layer_id: Optional[int]
    stable_key: Tuple[Any, ...]
    name_key: Tuple[Any, ...]


def _flatten_layers(layers: List[Layer]) -> List[_Node]:
    """Return all layers in deterministic pre-order traversal order."""

    nodes: List[_Node] = []

    def walk(
        children: List[Layer],
        parent: Optional[_Node],
        parent_path: Tuple[Optional[int], ...],
        parent_order: Tuple[int, ...],
        parent_stable: Tuple[Any, ...],
        parent_name: Tuple[Any, ...],
    ) -> None:
        counts: Dict[Tuple[str, str], int] = {}
        for index, layer in enumerate(children):
            kind = type(layer).__name__
            name = layer.name
            count_key = (kind, name)
            ordinal = counts.get(count_key, 0)
            counts[count_key] = ordinal + 1

            layer_id = layer.id
            path = parent_path + (layer_id,)
            order = parent_order + (index,)

            if layer_id is not None:
                stable_key: Tuple[Any, ...] = ("id", layer_id)
            else:
                stable_key = ("path", parent_stable, kind, name, ordinal)
            # Structural key independent of ids, used only to pair entries
            # that are missing an id on at least one side.
            name_key = ("path", parent_name, kind, name, ordinal)

            node = _Node(
                layer=layer,
                parent=parent,
                index=index,
                path=path,
                parent_path=parent_path,
                order=order,
                kind=kind,
                name=name,
                layer_id=layer_id,
                stable_key=stable_key,
                name_key=name_key,
            )
            nodes.append(node)

            if isinstance(layer, LayerGroup) and layer.layers:
                walk(
                    layer.layers,
                    node,
                    path,
                    order,
                    stable_key,
                    name_key,
                )

    walk(layers, None, (), (), ("root",), ("root",))
    return nodes


def _pair_by_key(
    old_nodes: List[_Node],
    new_nodes: List[_Node],
    key_attr: str,
    description: str,
    warnings: List[str],
) -> Tuple[List[Tuple[_Node, _Node]], List[_Node], List[_Node]]:
    """Pair nodes with equal keys, in document order for duplicate keys."""

    old_groups: Dict[Tuple[Any, ...], List[_Node]] = {}
    new_groups: Dict[Tuple[Any, ...], List[_Node]] = {}
    for node in old_nodes:
        old_groups.setdefault(getattr(node, key_attr), []).append(node)
    for node in new_nodes:
        new_groups.setdefault(getattr(node, key_attr), []).append(node)

    pairs: List[Tuple[_Node, _Node]] = []
    for key in sorted(set(old_groups) & set(new_groups), key=repr):
        old_group = old_groups[key]
        new_group = new_groups[key]
        if len(old_group) > 1 or len(new_group) > 1:
            warnings.append(
                "duplicate {} {} ({} old, {} new); paired in document "
                "order".format(description, key, len(old_group), len(new_group))
            )
        for old_node, new_node in zip(old_group, new_group):
            pairs.append((old_node, new_node))

    used_old = {id(pair[0]) for pair in pairs}
    used_new = {id(pair[1]) for pair in pairs}
    remaining_old = [node for node in old_nodes if id(node) not in used_old]
    remaining_new = [node for node in new_nodes if id(node) not in used_new]
    return pairs, remaining_old, remaining_new


def _match_layers(
    old_map: TiledMap, new_map: TiledMap, warnings: List[str]
) -> Tuple[List[Tuple[_Node, _Node, str]], List[_Node], List[_Node]]:
    """Match layers globally by id, then structurally when ids are missing."""

    old_nodes = _flatten_layers(old_map.layers)
    new_nodes = _flatten_layers(new_map.layers)

    id_pairs, old_rest, new_rest = _pair_by_key(
        [node for node in old_nodes if node.layer_id is not None],
        [node for node in new_nodes if node.layer_id is not None],
        "stable_key",
        "layer id",
        warnings,
    )
    pairs: List[Tuple[_Node, _Node, str]] = [
        (old_node, new_node, "id") for old_node, new_node in id_pairs
    ]
    # Nodes without an id never entered the id pairing; keep them all for the
    # structural pass below.
    old_rest = old_rest + [node for node in old_nodes if node.layer_id is None]
    new_rest = new_rest + [node for node in new_nodes if node.layer_id is None]

    # Pair remaining layers by structural path. This covers layers without an
    # id on both sides and a layer that lost or gained an id. Two layers that
    # both have different ids are never paired, so same-name/different-id
    # layers stay a removal plus an addition.
    old_by_path: Dict[Tuple[Any, ...], List[_Node]] = {}
    new_by_path: Dict[Tuple[Any, ...], List[_Node]] = {}
    for node in old_rest:
        old_by_path.setdefault(node.name_key, []).append(node)
    for node in new_rest:
        new_by_path.setdefault(node.name_key, []).append(node)

    path_pairs: List[Tuple[_Node, _Node, str]] = []
    for key in sorted(set(old_by_path) & set(new_by_path), key=repr):
        old_group = old_by_path[key]
        new_group = new_by_path[key]
        if len(old_group) > 1 or len(new_group) > 1:
            warnings.append(
                "duplicate layer path {} ({} old, {} new); paired in document "
                "order".format(key, len(old_group), len(new_group))
            )
        for old_node, new_node in zip(old_group, new_group):
            if old_node.layer_id is not None and new_node.layer_id is not None:
                continue
            path_pairs.append((old_node, new_node, "path"))

    paired_old = {id(pair[0]) for pair in path_pairs}
    paired_new = {id(pair[1]) for pair in path_pairs}
    old_rest = [node for node in old_rest if id(node) not in paired_old]
    new_rest = [node for node in new_rest if id(node) not in paired_new]

    for old_node, new_node, matched_by in path_pairs:
        if old_node.layer_id != new_node.layer_id:
            warnings.append(
                "layer at path {} matched by structure because an id is "
                "missing (old id {}, new id {})".format(
                    new_node.path, old_node.layer_id, new_node.layer_id
                )
            )
        elif old_node.layer_id is None:
            warnings.append(
                "layer at path {} matched by structure because neither side "
                "has an id".format(new_node.path)
            )

    return pairs + path_pairs, old_rest, new_rest


def _has_ancestor_in(node: _Node, target_ids: set) -> bool:
    """Whether a proper ancestor of ``node`` is in ``target_ids``."""

    parent = node.parent
    while parent is not None:
        if id(parent) in target_ids:
            return True
        parent = parent.parent
    return False


def _minimal_moved(
    items: List[Tuple[Any, Any]],
    old_index,
    new_index,
) -> set:
    """Return the smallest set of pairs that must be moved to reorder siblings.

    Entries are found by matching ids, so an insertion or removal shifts
    absolute indices without producing false moves. Among the surviving
    matched siblings, entries that already appear in the same relative order
    in both maps stay put; only those outside the longest such subsequence are
    reported as moved. This keeps a single reordered layer from marking every
    other sibling as moved.
    """

    old_sequence = sorted(items, key=lambda pair: old_index(pair[0]))
    new_position = {
        id(new): position
        for position, (_, new) in enumerate(
            sorted(items, key=lambda pair: new_index(pair[1]))
        )
    }
    sequence = [new_position[id(new)] for _, new in old_sequence]

    # Longest increasing subsequence (patience sorting).
    tails: List[int] = []
    tails_indices: List[int] = []
    predecessors: List[int] = [-1] * len(sequence)
    for index, value in enumerate(sequence):
        import bisect

        position = bisect.bisect_left(tails, value)
        if position == len(tails):
            tails.append(value)
            tails_indices.append(index)
        else:
            tails[position] = value
            tails_indices[position] = index
        if position > 0:
            predecessors[index] = tails_indices[position - 1]

    keep: set = set()
    if tails_indices:
        cursor = tails_indices[-1]
        while cursor != -1:
            keep.add(cursor)
            cursor = predecessors[cursor]

    moved: set = set()
    for index, (old, new) in enumerate(old_sequence):
        if index not in keep:
            moved.add(id(old))
            moved.add(id(new))
    return moved


def _sibling_rank_moves(
    pairs: List[Tuple[_Node, _Node]],
) -> set:
    """Return ids of nodes whose order among matched siblings changed."""

    groups: Dict[Tuple[Any, ...], List[Tuple[_Node, _Node]]] = {}
    for old_node, new_node in pairs:
        if old_node.parent_path != new_node.parent_path:
            continue
        groups.setdefault(new_node.parent_path, []).append((old_node, new_node))

    moved: set = set()
    for items in groups.values():
        moved |= _minimal_moved(
            items,
            lambda node: node.index,
            lambda node: node.index,
        )
    return moved


def _layer_changes(
    pairs: List[Tuple[_Node, _Node, str]],
    old_only: List[_Node],
    new_only: List[_Node],
) -> List[LayerChange]:
    rank_moves = _sibling_rank_moves([(old, new) for old, new, _ in pairs])
    order_by_layer: Dict[int, Tuple[int, ...]] = {}
    for old_node, new_node, _ in pairs:
        order_by_layer[id(old_node.layer)] = old_node.order
        order_by_layer[id(new_node.layer)] = new_node.order
    for node in old_only + new_only:
        order_by_layer[id(node.layer)] = node.order

    results: List[LayerChange] = []

    for old_node, new_node, matched_by in pairs:
        if old_node.kind != new_node.kind:
            results.append(
                LayerChange(
                    layer_id=(
                        new_node.layer_id
                        if new_node.layer_id is not None
                        else old_node.layer_id
                    ),
                    layer_name=new_node.name,
                    layer_type=new_node.kind,
                    path=new_node.path,
                    change=REPLACED,
                    old_path=old_node.path,
                    new_path=new_node.path,
                    old_index=old_node.index,
                    new_index=new_node.index,
                    old_parent_path=old_node.parent_path,
                    new_parent_path=new_node.parent_path,
                    matched_by=matched_by,
                    old_layer=old_node.layer,
                    new_layer=new_node.layer,
                )
            )
            continue

        field_changes = _diff_fields(
            old_node.layer, new_node.layer, _layer_fields(new_node.layer)
        )
        if isinstance(old_node.layer, TileLayer) and isinstance(
            new_node.layer, TileLayer
        ):
            old_chunked = old_node.layer.chunks is not None
            new_chunked = new_node.layer.chunks is not None
            if old_chunked != new_chunked:
                # Reported so replay can switch storage; the cells themselves
                # are still normalised and compared, which may add no cell
                # changes at all for a pure representation switch.
                field_changes["representation"] = (
                    "chunked" if old_chunked else "finite",
                    "chunked" if new_chunked else "finite",
                )
        moved = (
            old_node.parent_path != new_node.parent_path or id(old_node) in rank_moves
        )
        if not field_changes and not moved:
            continue

        results.append(
            LayerChange(
                layer_id=(
                    new_node.layer_id
                    if new_node.layer_id is not None
                    else old_node.layer_id
                ),
                layer_name=new_node.name,
                layer_type=new_node.kind,
                path=new_node.path,
                change=MOVED if moved else MODIFIED,
                field_changes=field_changes,
                old_path=old_node.path,
                new_path=new_node.path,
                old_index=old_node.index,
                new_index=new_node.index,
                old_parent_path=old_node.parent_path,
                new_parent_path=new_node.parent_path,
                matched_by=matched_by,
                old_layer=old_node.layer,
                new_layer=new_node.layer,
            )
        )

    removed_ids = {id(node) for node in old_only}
    for node in old_only:
        if _has_ancestor_in(node, removed_ids):
            continue
        results.append(
            LayerChange(
                layer_id=node.layer_id,
                layer_name=node.name,
                layer_type=node.kind,
                path=node.path,
                change=REMOVED,
                old_path=node.path,
                old_index=node.index,
                old_parent_path=node.parent_path,
                old_layer=node.layer,
            )
        )

    added_node_ids = {id(node) for node in new_only}
    for node in new_only:
        # A new descendant of a newly added group is already part of that
        # group's payload; reporting it separately would duplicate it.
        if _has_ancestor_in(node, added_node_ids):
            continue
        results.append(
            LayerChange(
                layer_id=node.layer_id,
                layer_name=node.name,
                layer_type=node.kind,
                path=node.path,
                change=ADDED,
                new_path=node.path,
                new_index=node.index,
                new_parent_path=node.parent_path,
                new_layer=node.layer,
            )
        )

    def sort_key(change: LayerChange) -> Tuple[Any, ...]:
        layer = change.old_layer if change.change == REMOVED else change.new_layer
        order = order_by_layer.get(id(layer), ())
        return (order, change.change, _path_key(change.path), change.layer_name)

    results.sort(key=sort_key)
    return results


# ---------------------------------------------------------------------------
# Object changes
# ---------------------------------------------------------------------------


@attr.s(auto_attribs=True)
class _ObjNode:
    obj: TiledObject
    layer_node: _Node
    index: int
    order: Tuple[int, ...]
    object_id: Optional[int]
    kind: str
    name: str
    path_key: Tuple[Any, ...]


def _flatten_objects(nodes: Iterable[_Node]) -> List[_ObjNode]:
    results: List[_ObjNode] = []
    for node in nodes:
        layer = node.layer
        if not isinstance(layer, ObjectLayer):
            continue
        counts: Dict[Tuple[str, str], int] = {}
        for index, obj in enumerate(layer.tiled_objects or []):
            kind = type(obj).__name__
            name = obj.name
            count_key = (kind, name)
            ordinal = counts.get(count_key, 0)
            counts[count_key] = ordinal + 1
            results.append(
                _ObjNode(
                    obj=obj,
                    layer_node=node,
                    index=index,
                    order=node.order + (index,),
                    object_id=getattr(obj, "id", None),
                    kind=kind,
                    name=name,
                    path_key=(node.name_key, kind, name, ordinal),
                )
            )
    return results


def _object_field_changes(
    old_obj: TiledObject,
    new_obj: TiledObject,
    old_resolver: _TilesetResolver,
    new_resolver: _TilesetResolver,
) -> Dict[str, Tuple[Any, Any]]:
    changes: Dict[str, Tuple[Any, Any]] = {}
    for field_name in _object_field_names(new_obj):
        old_value = getattr(old_obj, field_name, None)
        new_value = getattr(new_obj, field_name, None)
        if field_name == "gid" and isinstance(new_obj, Tile):
            old_ref = old_resolver.resolve(old_value)
            new_ref = new_resolver.resolve(new_value)
            if old_ref is None or new_ref is None:
                if not _values_equal(old_value, new_value):
                    changes[field_name] = (old_value, new_value)
            elif old_ref.semantic_key() != new_ref.semantic_key():
                changes[field_name] = (old_ref, new_ref)
        elif not _values_equal(old_value, new_value):
            changes[field_name] = (old_value, new_value)
    return changes


def _object_sibling_rank_moves(pairs: List[Tuple[_ObjNode, _ObjNode]]) -> set:
    groups: Dict[Tuple[Any, ...], List[Tuple[_ObjNode, _ObjNode]]] = {}
    for old_obj, new_obj in pairs:
        if old_obj.layer_node.path != new_obj.layer_node.path:
            continue
        groups.setdefault(new_obj.layer_node.path, []).append((old_obj, new_obj))

    moved: set = set()
    for items in groups.values():
        moved |= _minimal_moved(
            items,
            lambda obj: obj.index,
            lambda obj: obj.index,
        )
    return moved


def _object_changes(
    pairs: List[Tuple[_Node, _Node, str]],
    old_resolver: _TilesetResolver,
    new_resolver: _TilesetResolver,
    warnings: List[str],
) -> List[ObjectChange]:
    # Only matched object layers participate; objects in added, removed or
    # replaced layers are covered by the layer entry itself.
    object_layer_pairs = [
        (old_node, new_node)
        for old_node, new_node, _ in pairs
        if isinstance(old_node.layer, ObjectLayer)
        and isinstance(new_node.layer, ObjectLayer)
    ]
    paired_old = {id(old) for old, _ in object_layer_pairs}
    paired_new = {id(new) for _, new in object_layer_pairs}

    old_objs = [
        obj
        for obj in _flatten_objects(_iter_old_nodes(pairs))
        if id(obj.layer_node) in paired_old
    ]
    new_objs = [
        obj
        for obj in _flatten_objects(_iter_new_nodes(pairs))
        if id(obj.layer_node) in paired_new
    ]

    old_by_id: Dict[int, List[_ObjNode]] = {}
    new_by_id: Dict[int, List[_ObjNode]] = {}
    for obj in old_objs:
        if obj.object_id is not None:
            old_by_id.setdefault(obj.object_id, []).append(obj)
    for obj in new_objs:
        if obj.object_id is not None:
            new_by_id.setdefault(obj.object_id, []).append(obj)

    pairs_found: List[Tuple[_ObjNode, _ObjNode, str]] = []
    for object_id in sorted(set(old_by_id) & set(new_by_id)):
        old_group = old_by_id[object_id]
        new_group = new_by_id[object_id]
        if len(old_group) > 1 or len(new_group) > 1:
            warnings.append(
                "duplicate object id {} ({} old, {} new); paired in document "
                "order".format(object_id, len(old_group), len(new_group))
            )
        for old_obj, new_obj in zip(old_group, new_group):
            pairs_found.append((old_obj, new_obj, "id"))

    matched_old = {id(pair[0]) for pair in pairs_found}
    matched_new = {id(pair[1]) for pair in pairs_found}
    old_rest = [obj for obj in old_objs if id(obj) not in matched_old]
    new_rest = [obj for obj in new_objs if id(obj) not in matched_new]

    old_by_path: Dict[Tuple[Any, ...], List[_ObjNode]] = {}
    new_by_path: Dict[Tuple[Any, ...], List[_ObjNode]] = {}
    for obj in old_rest:
        old_by_path.setdefault(obj.path_key, []).append(obj)
    for obj in new_rest:
        new_by_path.setdefault(obj.path_key, []).append(obj)

    for key in sorted(set(old_by_path) & set(new_by_path), key=repr):
        old_group = old_by_path[key]
        new_group = new_by_path[key]
        if len(old_group) > 1 or len(new_group) > 1:
            warnings.append(
                "duplicate object path {} ({} old, {} new); paired in document "
                "order".format(key, len(old_group), len(new_group))
            )
        for old_obj, new_obj in zip(old_group, new_group):
            if old_obj.object_id is not None and new_obj.object_id is not None:
                continue
            pairs_found.append((old_obj, new_obj, "path"))
            if old_obj.object_id != new_obj.object_id:
                warnings.append(
                    "object at layer {} matched by structure because an id is "
                    "missing (old id {}, new id {})".format(
                        new_obj.layer_node.path,
                        old_obj.object_id,
                        new_obj.object_id,
                    )
                )
            elif old_obj.object_id is None:
                warnings.append(
                    "object at layer {} matched by structure because neither "
                    "side has an id".format(new_obj.layer_node.path)
                )

    matched_old = {id(pair[0]) for pair in pairs_found}
    matched_new = {id(pair[1]) for pair in pairs_found}
    old_rest = [obj for obj in old_objs if id(obj) not in matched_old]
    new_rest = [obj for obj in new_objs if id(obj) not in matched_new]

    rank_moves = _object_sibling_rank_moves(
        [(old_obj, new_obj) for old_obj, new_obj, _ in pairs_found]
    )

    results: List[ObjectChange] = []
    for old_obj, new_obj, matched_by in pairs_found:
        if old_obj.kind != new_obj.kind:
            results.append(
                ObjectChange(
                    object_id=(
                        new_obj.object_id
                        if new_obj.object_id is not None
                        else old_obj.object_id
                    ),
                    object_type=new_obj.kind,
                    layer_id=new_obj.layer_node.layer_id,
                    layer_path=new_obj.layer_node.path,
                    change=REPLACED,
                    old_layer_path=old_obj.layer_node.path,
                    new_layer_path=new_obj.layer_node.path,
                    old_index=old_obj.index,
                    new_index=new_obj.index,
                    matched_by=matched_by,
                    old_object=old_obj.obj,
                    new_object=new_obj.obj,
                    old_layer=old_obj.layer_node.layer,  # type: ignore[arg-type]
                    new_layer=new_obj.layer_node.layer,  # type: ignore[arg-type]
                )
            )
            continue

        field_changes = _object_field_changes(
            old_obj.obj, new_obj.obj, old_resolver, new_resolver
        )
        property_changes = _property_changes(
            old_obj.obj.properties, new_obj.obj.properties
        )
        moved = (
            old_obj.layer_node.path != new_obj.layer_node.path
            or id(old_obj) in rank_moves
        )
        if not field_changes and not property_changes and not moved:
            continue

        results.append(
            ObjectChange(
                object_id=(
                    new_obj.object_id
                    if new_obj.object_id is not None
                    else old_obj.object_id
                ),
                object_type=new_obj.kind,
                layer_id=new_obj.layer_node.layer_id,
                layer_path=new_obj.layer_node.path,
                change=MOVED if moved else MODIFIED,
                field_changes=field_changes,
                property_changes=property_changes,
                old_layer_path=old_obj.layer_node.path,
                new_layer_path=new_obj.layer_node.path,
                old_index=old_obj.index,
                new_index=new_obj.index,
                matched_by=matched_by,
                old_object=old_obj.obj,
                new_object=new_obj.obj,
                old_layer=old_obj.layer_node.layer,  # type: ignore[arg-type]
                new_layer=new_obj.layer_node.layer,  # type: ignore[arg-type]
            )
        )

    for obj in new_rest:
        results.append(
            ObjectChange(
                object_id=obj.object_id,
                object_type=obj.kind,
                layer_id=obj.layer_node.layer_id,
                layer_path=obj.layer_node.path,
                change=ADDED,
                new_layer_path=obj.layer_node.path,
                new_index=obj.index,
                new_object=obj.obj,
                new_layer=obj.layer_node.layer,  # type: ignore[arg-type]
            )
        )

    for obj in old_rest:
        results.append(
            ObjectChange(
                object_id=obj.object_id,
                object_type=obj.kind,
                layer_id=obj.layer_node.layer_id,
                layer_path=obj.layer_node.path,
                change=REMOVED,
                old_layer_path=obj.layer_node.path,
                old_index=obj.index,
                old_object=obj.obj,
                old_layer=obj.layer_node.layer,  # type: ignore[arg-type]
            )
        )

    def sort_key(change: ObjectChange) -> Tuple[Any, ...]:
        if change.change == REMOVED:
            layer_path = change.old_layer_path
            index = change.old_index
        else:
            layer_path = change.new_layer_path
            index = change.new_index
        return (
            _path_key(layer_path or ()),
            -1 if index is None else index,
            -1 if change.object_id is None else change.object_id,
            change.change,
        )

    results.sort(key=sort_key)
    return results


def _iter_old_nodes(pairs: List[Tuple[_Node, _Node, str]]) -> Iterator[_Node]:
    seen: set = set()
    for old_node, _, _ in pairs:
        for node in _iter_node_and_ancestors(old_node):
            if id(node) not in seen:
                seen.add(id(node))
                yield node


def _iter_new_nodes(pairs: List[Tuple[_Node, _Node, str]]) -> Iterator[_Node]:
    seen: set = set()
    for _, new_node, _ in pairs:
        for node in _iter_node_and_ancestors(new_node):
            if id(node) not in seen:
                seen.add(id(node))
                yield node


def _iter_node_and_ancestors(node: _Node) -> Iterator[_Node]:
    while node is not None:
        yield node
        node = node.parent  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Cell and chunk changes
# ---------------------------------------------------------------------------


def _cells_from_data(
    data: Optional[List[List[int]]], resolver: _TilesetResolver
) -> Dict[TileCoordinate, TileRef]:
    cells: Dict[TileCoordinate, TileRef] = {}
    for row_index, row in enumerate(data or []):
        for column_index, gid in enumerate(row):
            ref = resolver.resolve(gid)
            if ref is not None:
                cells[TileCoordinate(column_index, row_index)] = ref
    return cells


def _cells_from_chunk(
    chunk: Chunk, resolver: _TilesetResolver
) -> Dict[TileCoordinate, TileRef]:
    cells: Dict[TileCoordinate, TileRef] = {}
    origin_x = int(chunk.coordinates.x)
    origin_y = int(chunk.coordinates.y)
    for row_index, row in enumerate(chunk.data or []):
        for column_index, gid in enumerate(row):
            ref = resolver.resolve(gid)
            if ref is not None:
                cells[TileCoordinate(origin_x + column_index, origin_y + row_index)] = (
                    ref
                )
    return cells


def _chunk_key(chunk: Chunk) -> TileCoordinate:
    return TileCoordinate(int(chunk.coordinates.x), int(chunk.coordinates.y))


def _chunk_map(layer: TileLayer) -> Dict[TileCoordinate, Chunk]:
    return {_chunk_key(chunk): chunk for chunk in layer.chunks or []}


def _cell_sort_key(coordinate: TileCoordinate) -> Tuple[int, int]:
    return (coordinate.y, coordinate.x)


def _compare_cells(
    old_cells: Dict[TileCoordinate, TileRef],
    new_cells: Dict[TileCoordinate, TileRef],
    layer: TileLayer,
    old_chunks: Dict[TileCoordinate, Chunk],
    new_chunks: Dict[TileCoordinate, Chunk],
) -> List[CellChange]:
    """Compare two cell maps; ``chunk_of`` maps a cell to its chunk (optional)."""

    changes: List[CellChange] = []

    def chunk_of(
        coordinate: TileCoordinate, chunks: Dict[TileCoordinate, Chunk]
    ) -> Optional[TileCoordinate]:
        for key, chunk in chunks.items():
            if key.x <= coordinate.x < key.x + int(
                chunk.size.width
            ) and key.y <= coordinate.y < key.y + int(chunk.size.height):
                return key
        return None

    for coordinate in sorted(set(old_cells) | set(new_cells), key=_cell_sort_key):
        old_ref = old_cells.get(coordinate)
        new_ref = new_cells.get(coordinate)
        if old_ref is not None and new_ref is not None:
            if old_ref.semantic_key() == new_ref.semantic_key():
                continue
            change = MODIFIED
        elif new_ref is not None:
            change = ADDED
        else:
            change = REMOVED

        chunk_coordinate = chunk_of(coordinate, new_chunks)
        if chunk_coordinate is None:
            chunk_coordinate = chunk_of(coordinate, old_chunks)

        changes.append(
            CellChange(
                layer_id=layer.id,
                layer_path=(),  # filled in by the caller
                coordinate=coordinate,
                change=change,
                old=old_ref,
                new=new_ref,
                chunk_coordinate=chunk_coordinate,
                layer=layer,
            )
        )
    return changes


def _tile_layer_changes(
    old_node: _Node,
    new_node: _Node,
    old_resolver: _TilesetResolver,
    new_resolver: _TilesetResolver,
) -> Tuple[List[CellChange], List[ChunkChange]]:
    old_layer = old_node.layer
    new_layer = new_node.layer
    if not isinstance(old_layer, TileLayer) or not isinstance(new_layer, TileLayer):
        return [], []

    old_chunks = _chunk_map(old_layer) if old_layer.chunks is not None else {}
    new_chunks = _chunk_map(new_layer) if new_layer.chunks is not None else {}

    cell_changes: List[CellChange] = []
    chunk_changes: List[ChunkChange] = []

    old_is_chunked = old_layer.chunks is not None
    new_is_chunked = new_layer.chunks is not None

    if old_is_chunked and new_is_chunked:
        # Compare chunk structure first, then cells only for chunks that exist
        # on both sides with the same size.
        compared_chunks: set = set()
        for coordinate in sorted(set(old_chunks) | set(new_chunks), key=_cell_sort_key):
            old_chunk = old_chunks.get(coordinate)
            new_chunk = new_chunks.get(coordinate)
            if old_chunk is None:
                assert new_chunk is not None
                chunk_changes.append(
                    ChunkChange(
                        layer_id=new_layer.id,
                        layer_path=new_node.path,
                        coordinate=coordinate,
                        change=ADDED,
                        new_size=(new_chunk.size.width, new_chunk.size.height),
                        chunk=new_chunk,
                        layer=new_layer,
                    )
                )
            elif new_chunk is None:
                chunk_changes.append(
                    ChunkChange(
                        layer_id=new_layer.id,
                        layer_path=new_node.path,
                        coordinate=coordinate,
                        change=REMOVED,
                        old_size=(old_chunk.size.width, old_chunk.size.height),
                        layer=new_layer,
                    )
                )
            elif old_chunk.size != new_chunk.size:
                assert new_chunk is not None
                chunk_changes.append(
                    ChunkChange(
                        layer_id=new_layer.id,
                        layer_path=new_node.path,
                        coordinate=coordinate,
                        change=RESIZED,
                        old_size=(old_chunk.size.width, old_chunk.size.height),
                        new_size=(new_chunk.size.width, new_chunk.size.height),
                        chunk=new_chunk,
                        layer=new_layer,
                    )
                )
            else:
                compared_chunks.add(coordinate)

        old_cells: Dict[TileCoordinate, TileRef] = {}
        new_cells: Dict[TileCoordinate, TileRef] = {}
        for key in compared_chunks:
            old_cells.update(_cells_from_chunk(old_chunks[key], old_resolver))
            new_cells.update(_cells_from_chunk(new_chunks[key], new_resolver))
        cell_changes = _compare_cells(
            old_cells, new_cells, new_layer, old_chunks, new_chunks
        )
    else:
        old_cells = _cells_from_data(old_layer.data, old_resolver)
        new_cells = _cells_from_data(new_layer.data, new_resolver)
        if old_is_chunked:
            for chunk in old_chunks.values():
                old_cells.update(_cells_from_chunk(chunk, old_resolver))
        if new_is_chunked:
            for chunk in new_chunks.values():
                new_cells.update(_cells_from_chunk(chunk, new_resolver))
        cell_changes = _compare_cells(old_cells, new_cells, new_layer, {}, {})

    for change in cell_changes:
        object.__setattr__(change, "layer_path", new_node.path)
    return cell_changes, chunk_changes


# ---------------------------------------------------------------------------
# Map-level changes
# ---------------------------------------------------------------------------


def _map_changes(old_map: TiledMap, new_map: TiledMap) -> List[MapChange]:
    changes: List[MapChange] = []
    for field_name in _MAP_FIELDS:
        old_value = getattr(old_map, field_name, None)
        new_value = getattr(new_map, field_name, None)
        if not _values_equal(old_value, new_value):
            changes.append(
                MapChange(field=field_name, before=old_value, after=new_value)
            )
    return changes


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def diff_maps(old_map: TiledMap, new_map: TiledMap) -> MapDiff:
    """Compare two parsed maps and return a deterministic change report.

    Args:
        old_map: The map the previewer currently has loaded.
        new_map: The freshly parsed map saved by the editor.

    Returns:
        MapDiff: The minimal set of map, tileset, layer, object, chunk and
        tile-cell changes needed to turn ``old_map`` into ``new_map``.

    Neither input is modified.
    """

    warnings: List[str] = []

    map_changes = _map_changes(old_map, new_map)
    tileset_changes, old_tokens, new_tokens = _match_tilesets(
        old_map, new_map, warnings
    )
    old_resolver = _TilesetResolver(old_map.tilesets, old_tokens)
    new_resolver = _TilesetResolver(new_map.tilesets, new_tokens)

    layer_pairs, old_only, new_only = _match_layers(old_map, new_map, warnings)

    cell_changes: List[CellChange] = []
    chunk_changes: List[ChunkChange] = []
    for old_node, new_node, matched_by in layer_pairs:
        if matched_by != "id" and old_node.kind != new_node.kind:
            continue
        cells, chunks = _tile_layer_changes(
            old_node, new_node, old_resolver, new_resolver
        )
        cell_changes.extend(cells)
        chunk_changes.extend(chunks)

    object_changes = _object_changes(layer_pairs, old_resolver, new_resolver, warnings)
    layer_changes = _layer_changes(layer_pairs, old_only, new_only)

    cell_changes.sort(
        key=lambda change: (
            _path_key(change.layer_path),
            change.coordinate.y,
            change.coordinate.x,
        )
    )
    chunk_changes.sort(
        key=lambda change: (
            _path_key(change.layer_path),
            change.coordinate.y,
            change.coordinate.x,
        )
    )
    sorted_map_changes = tuple(sorted(map_changes, key=lambda change: change.field))

    return MapDiff(
        map_changes=sorted_map_changes,
        tileset_changes=tuple(tileset_changes),
        layer_changes=tuple(layer_changes),
        object_changes=tuple(object_changes),
        cell_changes=tuple(cell_changes),
        chunk_changes=tuple(chunk_changes),
        warnings=tuple(sorted(set(warnings))),
    )


# Metadata that does not affect a preview and is therefore left untouched by
# the report and by replay.
_IGNORED_MAP_FIELDS = (
    "map_file",
    "version",
    "tiled_version",
    "next_layer_id",
    "next_object_id",
)


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------


def _deepcopy(value: Any) -> Any:
    return copy.deepcopy(value)


def _find_layer(root: TiledMap, path: Tuple[Optional[int], ...]) -> Optional[Layer]:
    layers = root.layers
    node: Optional[Layer] = None
    for depth, layer_id in enumerate(path):
        node = None
        for candidate in layers:
            if candidate.id == layer_id:
                node = candidate
                break
        if node is None:
            return None
        if depth < len(path) - 1:
            if not isinstance(node, LayerGroup) or node.layers is None:
                return None
            layers = node.layers
    return node


def _parent_list(
    root: TiledMap, parent_path: Tuple[Optional[int], ...]
) -> Optional[List[Layer]]:
    if not parent_path:
        return root.layers
    parent = _find_layer(root, parent_path)
    if not isinstance(parent, LayerGroup):
        return None
    if parent.layers is None:
        parent.layers = []
    return parent.layers


def _remove_layer(
    root: TiledMap, parent_path: Tuple[Optional[int], ...], layer_id: Optional[int]
) -> bool:
    layers = _parent_list(root, parent_path)
    if layers is None:
        return False
    for index, candidate in enumerate(layers):
        if candidate.id == layer_id:
            del layers[index]
            return True
    return False


def _apply_layer_changes(root: TiledMap, changes: Tuple[LayerChange, ...]) -> None:
    """Replay layer structure and fields.

    Removals and replacements are applied by identity. Updates only touch
    fields. Placement then runs per parent list over the *final* entries
    (surviving movers plus additions) using their final indices, so index
    arithmetic does not depend on the order the changes are listed in.
    """

    removals = [c for c in changes if c.change == REMOVED]
    additions = [c for c in changes if c.change == ADDED]
    replacements = [c for c in changes if c.change == REPLACED]
    updates = [c for c in changes if c.change in (MODIFIED, MOVED)]

    movers = [
        change
        for change in updates
        if change.change == MOVED and change.new_index is not None
    ]

    # Detach every mover from its old parent before removals run. A child that
    # moves out of a group which is itself removed must survive; the group
    # removal would otherwise take it with it.
    detached: Dict[int, Tuple[LayerChange, Layer]] = {}
    for change in movers:
        old_parent = change.old_parent_path or ()
        new_parent = change.new_parent_path or ()
        layer = _find_layer(root, change.old_path or ())
        if layer is None:
            continue
        if old_parent != new_parent:
            _remove_layer(
                root,
                old_parent,
                change.old_path[-1] if change.old_path else None,
            )
        detached[id(change)] = (change, layer)

    # Remove deepest entries first so their parents are still locatable.
    removals.sort(key=lambda change: len(change.old_path or ()), reverse=True)
    for change in removals:
        _remove_layer(
            root,
            change.old_parent_path or (),
            change.old_path[-1] if change.old_path else None,
        )

    # Replace in place so unaffected siblings keep their payloads.
    replacements.sort(key=lambda change: len(change.old_path or ()))
    for change in replacements:
        layers = _parent_list(root, change.old_parent_path or ())
        if layers is None or change.new_layer is None:
            continue
        position = None
        if change.old_path:
            for candidate_index, candidate in enumerate(layers):
                if candidate.id == change.old_path[-1]:
                    position = candidate_index
                    break
        if position is not None:
            layers[position] = _deepcopy(change.new_layer)
        else:
            layers.append(_deepcopy(change.new_layer))

    for change in updates:
        layer = _find_layer(root, change.old_path or ())
        if layer is None:
            layer = _find_layer(root, change.new_path or ())
        if layer is None:
            continue
        for field_name, (_, after) in change.field_changes.items():
            if field_name == "representation" and isinstance(layer, TileLayer):
                # The whole storage payload is carried on the change, so the
                # switch can be replayed without synthesising chunks.
                if change.new_layer is not None:
                    layer.data = _deepcopy(getattr(change.new_layer, "data", None))
                    layer.chunks = _deepcopy(getattr(change.new_layer, "chunks", None))
                continue
            setattr(layer, field_name, _deepcopy(after))

    # Place survivors and additions. Build the final list for every parent
    # that contains a mover or addition, then splice it in.
    placeable: Dict[Tuple[Optional[int], ...], List[_NodeLike]] = {}

    # An added layer's snapshot may contain descendants that also exist on the
    # old map and move into it. Those descendants are placed separately, so
    # strip them from the snapshot to avoid duplicating them.
    moved_ids = {
        change.new_path[-1]
        for change in movers
        if change.new_path and change.new_path[-1] is not None
    } | {
        change.path[-1]
        for change in movers
        if change.path and change.path[-1] is not None
    }

    def prune(layer: Layer) -> Layer:
        if isinstance(layer, LayerGroup) and layer.layers:
            layer.layers = [
                prune(child) for child in layer.layers if child.id not in moved_ids
            ]
        return layer

    additions.sort(key=lambda change: len(change.new_path or ()))
    for change in additions:
        parent_path = change.new_parent_path or ()
        payload = _deepcopy(change.new_layer)
        if payload is not None and moved_ids:
            payload = prune(payload)
        placeable.setdefault(parent_path, []).append(
            _NodeLike(
                payload=payload,
                index=change.new_index,
                key=("add", _path_key(change.new_path or ())),
            )
        )

    # A layer that already existed elsewhere cannot appear twice. If an added
    # group snapshot carries a child that exists on the old map but was not
    # matched (because it was copied rather than moved), the old copy is
    # removed so the added snapshot is the single source of truth.
    for change in movers:
        detached_entry = detached.get(id(change))
        if detached_entry is None:
            continue
        layer = detached_entry[1]
        placeable.setdefault(change.new_parent_path or (), []).append(
            _NodeLike(
                payload=layer,
                index=change.new_index,
                key=("move", _path_key(change.new_path or ())),
            )
        )

    for parent_path in sorted(placeable, key=len):
        layers = _parent_list(root, parent_path)
        if layers is None:
            continue
        desired = placeable[parent_path]
        payloads = {id(item.payload) for item in desired}
        others = [layer for layer in layers if id(layer) not in payloads]
        ordered = sorted(
            desired,
            key=lambda item: (
                item.index if item.index is not None else len(others),
                item.key,
            ),
        )
        final: List[Layer] = []
        remaining = list(others)
        for item in ordered:
            index = item.index
            if index is None:
                index = len(final)
            index = min(index, len(final) + len(remaining))
            while len(final) < index and remaining:
                final.append(remaining.pop(0))
            if item.payload is not None:
                final.append(item.payload)
        final.extend(remaining)
        layers[:] = final


@attr.s(auto_attribs=True)
class _NodeLike:
    payload: Optional[Layer]
    index: Optional[int]
    key: Tuple[Any, ...]


def _iter_layers(layers: List[Layer]) -> Iterator[Layer]:
    for layer in layers:
        yield layer
        if isinstance(layer, LayerGroup) and layer.layers:
            yield from _iter_layers(layer.layers)


def _remap_gid(raw_gid: int, remaps: List[Tuple[int, int, int]]) -> int:
    """Translate a raw GID between old and new tileset ranges.

    ``remaps`` contains ``(old_firstgid, new_firstgid, tile_count)`` for every
    matched tileset whose ``firstgid`` changed. Intervals are checked from the
    highest ``old_firstgid`` down, mirroring GID resolution.
    """

    flags = raw_gid & ~_GID_MASK
    base = raw_gid & _GID_MASK
    for old_first, new_first, tile_count in remaps:
        if old_first <= base < old_first + tile_count:
            return flags | (new_first + (base - old_first))
    return raw_gid


def _apply_tileset_remaps(root: TiledMap, changes: Tuple[TilesetChange, ...]) -> None:
    """Rewrite raw GIDs in place for matched tilesets whose firstgid moved.

    This is what makes a pure ``remapped`` report replayable without emitting
    one cell change per tile: the cell identity did not change, only the
    numbering did.
    """

    remaps: List[Tuple[int, int, int]] = []
    for change in changes:
        if change.old is None or change.new is None:
            continue
        if change.old.firstgid == change.new.firstgid:
            continue
        if change.tileset is None:
            continue
        remaps.append(
            (change.old.firstgid, change.new.firstgid, change.tileset.tile_count)
        )
    if not remaps:
        return
    remaps.sort(key=lambda item: item[0], reverse=True)

    for layer in _iter_layers(root.layers):
        if isinstance(layer, TileLayer):
            for row in layer.data or []:
                for index, gid in enumerate(row):
                    row[index] = _remap_gid(gid, remaps)
            for chunk in layer.chunks or []:
                for row in chunk.data:
                    for index, gid in enumerate(row):
                        row[index] = _remap_gid(gid, remaps)
        elif isinstance(layer, ObjectLayer):
            for obj in layer.tiled_objects or []:
                if isinstance(obj, Tile):
                    obj.gid = _remap_gid(obj.gid, remaps)


def _apply_tileset_changes(root: TiledMap, changes: Tuple[TilesetChange, ...]) -> None:
    for change in changes:
        if change.change == REMOVED:
            if change.old is not None:
                root.tilesets.pop(change.old.firstgid, None)
            continue
        if change.new is None or change.tileset is None:
            continue
        if change.old is not None and change.old.firstgid != change.new.firstgid:
            root.tilesets.pop(change.old.firstgid, None)
        root.tilesets[change.new.firstgid] = _deepcopy(change.tileset)


def _write_cell(layer: TileLayer, coordinate: TileCoordinate, raw_gid: int) -> bool:
    """Write one raw GID into a layer in place. Returns False on mismatch."""

    if layer.data is not None:
        if coordinate.y < 0 or coordinate.y >= len(layer.data):
            return False
        row = layer.data[coordinate.y]
        if coordinate.x < 0 or coordinate.x >= len(row):
            return False
        row[coordinate.x] = raw_gid
        return True

    for chunk in layer.chunks or []:
        key = _chunk_key(chunk)
        local_x = coordinate.x - key.x
        local_y = coordinate.y - key.y
        if 0 <= local_x < int(chunk.size.width) and 0 <= local_y < int(
            chunk.size.height
        ):
            chunk.data[local_y][local_x] = raw_gid
            return True
    return False


def _apply_cell_and_chunk_changes(
    root: TiledMap,
    diff: MapDiff,
) -> None:
    layers_by_path: Dict[Tuple[Optional[int], ...], TileLayer] = {}
    for change in diff.cell_changes + diff.chunk_changes:
        if change.layer_path in layers_by_path:
            continue
        layer = _find_layer(root, change.layer_path)
        if isinstance(layer, TileLayer):
            layers_by_path[change.layer_path] = layer

    replace_paths: set = set()

    for change in diff.chunk_changes:
        layer = layers_by_path.get(change.layer_path)
        if layer is None:
            continue
        chunks = layer.chunks if layer.chunks is not None else []
        layers_by_path[change.layer_path].chunks = chunks
        index = None
        for position, chunk in enumerate(chunks):
            if _chunk_key(chunk) == change.coordinate:
                index = position
                break
        if change.change == REMOVED:
            if index is not None:
                del chunks[index]
        elif change.change == ADDED:
            if change.chunk is not None:
                chunks.append(_deepcopy(change.chunk))
        else:  # RESIZED
            if change.chunk is not None:
                if index is not None:
                    chunks[index] = _deepcopy(change.chunk)
                else:
                    chunks.append(_deepcopy(change.chunk))

    for change in diff.cell_changes:
        if change.layer_path in replace_paths:
            continue
        layer = layers_by_path.get(change.layer_path)
        if layer is None:
            continue
        raw_gid = 0 if change.new is None else change.new.raw_gid
        if not _write_cell(layer, change.coordinate, raw_gid):
            # The old representation cannot hold this coordinate (for example
            # a finite layer that became infinite). Fall back to replacing the
            # layer's tile data from the new payload.
            if change.layer is not None:
                layer.data = _deepcopy(change.layer.data)
                layer.chunks = _deepcopy(change.layer.chunks)
                replace_paths.add(change.layer_path)


def _find_object(
    layer: ObjectLayer, object_id: Optional[int], index: Optional[int]
) -> Tuple[Optional[int], Optional[TiledObject]]:
    objects = layer.tiled_objects or []
    # Identity beats index: inserting or removing a sibling shifts indices of
    # unaffected objects.
    if object_id is not None:
        for position, candidate in enumerate(objects):
            if candidate.id == object_id:
                return position, candidate
    if index is not None and 0 <= index < len(objects):
        return index, objects[index]
    return None, None


def _apply_object_changes(root: TiledMap, changes: Tuple[ObjectChange, ...]) -> None:
    removals = [c for c in changes if c.change == REMOVED]
    additions = [c for c in changes if c.change == ADDED]
    replacements = [c for c in changes if c.change == REPLACED]
    updates = [c for c in changes if c.change in (MODIFIED, MOVED)]

    # Moves into other layers are applied first, so indices within a single
    # layer can then be resolved by a straightforward ordered re-insert.
    cross_layer_updates = [
        c for c in updates if (c.old_layer_path or ()) != (c.new_layer_path or ())
    ]
    in_place_updates = [
        c for c in updates if (c.old_layer_path or ()) == (c.new_layer_path or ())
    ]

    for change in removals:
        layer = _find_layer(root, change.old_layer_path or ())
        if not isinstance(layer, ObjectLayer):
            continue
        position, _ = _find_object(layer, change.object_id, change.old_index)
        if position is not None:
            del layer.tiled_objects[position]

    for change in replacements:
        layer = _find_layer(root, change.old_layer_path or ())
        if not isinstance(layer, ObjectLayer) or change.new_object is None:
            continue
        position, _ = _find_object(layer, change.object_id, change.old_index)
        if position is not None:
            layer.tiled_objects[position] = _deepcopy(change.new_object)

    for change in additions:
        layer = _find_layer(root, change.new_layer_path or ())
        if not isinstance(layer, ObjectLayer) or change.new_object is None:
            continue
        index = (
            change.new_index
            if change.new_index is not None
            else len(layer.tiled_objects)
        )
        layer.tiled_objects.insert(
            min(index, len(layer.tiled_objects)), _deepcopy(change.new_object)
        )

    for change in cross_layer_updates:
        layer = _find_layer(root, change.old_layer_path or ())
        if not isinstance(layer, ObjectLayer):
            continue
        position, obj = _find_object(layer, change.object_id, change.old_index)
        if obj is None:
            continue
        _update_object_fields(obj, change)
        assert position is not None
        del layer.tiled_objects[position]
        new_layer = _find_layer(root, change.new_layer_path or ())
        if isinstance(new_layer, ObjectLayer):
            index = (
                change.new_index
                if change.new_index is not None
                else len(new_layer.tiled_objects)
            )
            new_layer.tiled_objects.insert(
                min(index, len(new_layer.tiled_objects)), obj
            )

    for change in in_place_updates:
        layer = _find_layer(root, change.old_layer_path or ())
        if not isinstance(layer, ObjectLayer):
            continue
        _, obj = _find_object(layer, change.object_id, change.old_index)
        if obj is None:
            continue
        _update_object_fields(obj, change)

    # Reorder replayed objects per layer as a batch: remove all movers, then
    # insert them at their final indices in ascending order. Applying one
    # index at a time would be wrong after an earlier insertion shifted the
    # rest.
    reorder_groups: Dict[Tuple[Optional[int], ...], List[ObjectChange]] = {}
    for change in in_place_updates:
        # Every entry reported as moved inside its layer takes part in the
        # batch, even if its absolute index happens to be unchanged: a
        # previous removal may shift it anyway.
        if change.change != MOVED or change.new_index is None:
            continue
        reorder_groups.setdefault(change.new_layer_path or (), []).append(change)

    for layer_path, reorder_changes in reorder_groups.items():
        layer = _find_layer(root, layer_path)
        if not isinstance(layer, ObjectLayer):
            continue
        objects = layer.tiled_objects
        moving: List[Tuple[ObjectChange, TiledObject]] = []
        for change in reorder_changes:
            found = None
            for obj in objects:
                if change.object_id is not None and obj.id == change.object_id:
                    found = obj
                    break
            if found is None and change.new_index is not None:
                if 0 <= change.new_index < len(objects):
                    found = objects[change.new_index]
            if found is not None:
                moving.append((change, found))
        for _, obj in moving:
            for position, candidate in enumerate(objects):
                if candidate is obj:
                    del objects[position]
                    break
        for change, obj in sorted(
            moving,
            key=lambda item: (
                item[0].new_index if item[0].new_index is not None else 0
            ),
        ):
            index = change.new_index if change.new_index is not None else len(objects)
            objects.insert(min(index, len(objects)), obj)


def _update_object_fields(obj: TiledObject, change: ObjectChange) -> None:
    properties = dict(obj.properties or {})
    for prop_change in change.property_changes:
        if prop_change.change == REMOVED:
            properties.pop(prop_change.name, None)
        else:
            properties[prop_change.name] = _deepcopy(prop_change.after)
    obj.properties = properties

    for field_name, (_, after) in change.field_changes.items():
        if field_name == "gid" and isinstance(change.new_object, Tile):
            # Only Tile objects have a gid; the change payload is the parsed
            # new object, so its raw gid is authoritative.
            obj.gid = change.new_object.gid  # type: ignore[attr-defined]
        else:
            setattr(obj, field_name, _deepcopy(after))


def apply_map_diff(old_map: TiledMap, diff: MapDiff) -> TiledMap:
    """Apply a report to a deep copy of ``old_map`` and return the result.

    ``old_map`` and every object reachable from it are left untouched. The
    returned map is equivalent to the map ``diff`` was generated against,
    except that team metadata ignored by :func:`diff_maps` (file path,
    Tiled/format versions and next ids) keeps its old value.

    Args:
        old_map: The map the diff was computed from.
        diff: A report returned by :func:`diff_maps`.

    Returns:
        TiledMap: A replay of ``diff`` on top of ``old_map``.
    """

    result = _deepcopy(old_map)

    for change in diff.map_changes:
        setattr(result, change.field, _deepcopy(change.after))

    _apply_tileset_changes(result, diff.tileset_changes)
    _apply_tileset_remaps(result, diff.tileset_changes)

    # Layers, cells and objects all address entries by the diff's before/after
    # state, so they are applied in report order: structure first, then
    # content.
    _apply_layer_changes(result, diff.layer_changes)
    _apply_cell_and_chunk_changes(result, diff)
    _apply_object_changes(result, diff.object_changes)

    # Keep the old ignored metadata, then make sure a replay never claims an
    # id that the new map does not use.
    for field_name in _IGNORED_MAP_FIELDS:
        setattr(result, field_name, getattr(old_map, field_name))

    return result


__all__ = [
    "ADDED",
    "REMOVED",
    "MODIFIED",
    "MOVED",
    "REPLACED",
    "REMAPPED",
    "RESIZED",
    "CellChange",
    "ChunkChange",
    "LayerChange",
    "MapChange",
    "MapDiff",
    "ObjectChange",
    "PropertyChange",
    "TileCoordinate",
    "TileDefinitionChange",
    "TileRef",
    "TilesetChange",
    "TilesetRef",
    "apply_map_diff",
    "diff_maps",
]
