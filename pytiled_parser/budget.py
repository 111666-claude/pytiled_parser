"""Configurable resource budgets for parsing maps and tilesets.

This module protects importers from hostile or corrupt map files — for
example a tiny compressed tile layer that expands to hundreds of millions of
tiles (a decompression bomb), absurd declared dimensions, or deeply nested
group layers. Limits are enforced with identical counting semantics across
the JSON and TMX parsers, for finite layers and infinite-map chunks alike,
and decompression is capped in-stream so a compressed stream cannot expand
past the budget in memory.

All limits default to values far beyond any normal map, so default parsing
behavior is unchanged for legitimate files.
"""

import base64
import importlib.util
import zlib
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, List, Optional, Union

from pytiled_parser.exception import BudgetExceededError, MalformedMapError

# See the note in pytiled_parser.parsers.json.layer about the optional zstd
# dependency. zstd decompression cannot be output-capped with the `zstd`
# package's simple API, so its output is measured after the fact; every other
# compression format is capped while decompressing.
zstd_spec = importlib.util.find_spec("zstd")
if zstd_spec:  # pragma: no cover
    import zstd
else:
    zstd = None

# 4 bytes per tile GID in encoded layer data
_BYTES_PER_TILE = 4

DEFAULT_MAX_INPUT_BYTES = 256 * 1024 * 1024  # 256 MiB
DEFAULT_MAX_LAYERS = 10_000
DEFAULT_MAX_OBJECTS = 1_000_000
DEFAULT_MAX_TILES_PER_LAYER = 16_777_216  # 4096 x 4096 tiles
DEFAULT_MAX_DECOMPRESSED_BYTES = 256 * 1024 * 1024  # 256 MiB
DEFAULT_MAX_GROUP_DEPTH = 32


class ParseBudget:
    """Resource limits applied while parsing a map or tileset.

    Args:
        max_input_bytes: Maximum total size, in bytes, of input files read
            during a single parse (the map file plus any external tilesets).
        max_layers: Maximum total number of layers, counting nested group
            layers and their children.
        max_objects: Maximum total number of objects across all object layers.
        max_tiles_per_layer: Maximum number of tiles in a single layer. For
            infinite maps this is the sum of the tiles in the layer's chunks.
            Declared dimensions are checked before any data is decoded, so an
            absurd width/height claim is rejected up front.
        max_decompressed_bytes: Maximum total number of bytes produced by
            decompressing layer data during a single parse. Enforcement is
            done while decompressing, so output never exceeds this limit in
            memory (except for zstd, which is measured after decompression).
        max_group_depth: Maximum nesting depth of group layers.

    All limits must be positive integers. The defaults are far beyond any
    normal map and exist to reject hostile or corrupt files, not to
    constrain legitimate ones.
    """

    def __init__(
        self,
        max_input_bytes: int = DEFAULT_MAX_INPUT_BYTES,
        max_layers: int = DEFAULT_MAX_LAYERS,
        max_objects: int = DEFAULT_MAX_OBJECTS,
        max_tiles_per_layer: int = DEFAULT_MAX_TILES_PER_LAYER,
        max_decompressed_bytes: int = DEFAULT_MAX_DECOMPRESSED_BYTES,
        max_group_depth: int = DEFAULT_MAX_GROUP_DEPTH,
    ) -> None:
        limits = {
            "max_input_bytes": max_input_bytes,
            "max_layers": max_layers,
            "max_objects": max_objects,
            "max_tiles_per_layer": max_tiles_per_layer,
            "max_decompressed_bytes": max_decompressed_bytes,
            "max_group_depth": max_group_depth,
        }
        for name, value in limits.items():
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError(f"{name} must be a positive integer, got {value!r}")

        self.max_input_bytes = max_input_bytes
        self.max_layers = max_layers
        self.max_objects = max_objects
        self.max_tiles_per_layer = max_tiles_per_layer
        self.max_decompressed_bytes = max_decompressed_bytes
        self.max_group_depth = max_group_depth

    def __repr__(self) -> str:
        return (
            f"ParseBudget(max_input_bytes={self.max_input_bytes}, "
            f"max_layers={self.max_layers}, max_objects={self.max_objects}, "
            f"max_tiles_per_layer={self.max_tiles_per_layer}, "
            f"max_decompressed_bytes={self.max_decompressed_bytes}, "
            f"max_group_depth={self.max_group_depth})"
        )


class BudgetTracker:
    """Tracks resource consumption against a ParseBudget during one parse.

    A single tracker is shared by the whole parse of one map (including
    nested group layers, infinite-map chunks, and external tilesets) so that
    the JSON and TMX parsers enforce identical counting semantics.
    """

    def __init__(
        self,
        budget: Optional[ParseBudget] = None,
        file: Optional[Union[str, Path]] = None,
    ) -> None:
        self.budget = budget if budget is not None else ParseBudget()
        self.file = file
        self.input_bytes = 0
        self.layer_count = 0
        self.object_count = 0
        self.decompressed_bytes = 0
        self._layer_stack: List[str] = []

    @property
    def layer_position(self) -> Optional[str]:
        """Position of the layer currently being parsed, e.g. 'group/child'."""
        if not self._layer_stack:
            return None
        return "/".join(self._layer_stack)

    def exceed(self, message: str, limit: int, actual: int) -> None:
        """Raise a BudgetExceededError with the current file/layer context."""
        raise BudgetExceededError(
            message,
            file=self.file,
            layer=self.layer_position,
            limit=limit,
            actual=actual,
        )

    def malformed(self, message: str) -> None:
        """Raise a MalformedMapError with the current file/layer context."""
        raise MalformedMapError(message, file=self.file, layer=self.layer_position)

    def add_input_bytes(self, size: int, source: Union[str, Path]) -> None:
        """Count bytes read from an input file against the budget."""
        self.input_bytes += size
        if self.input_bytes > self.budget.max_input_bytes:
            self.exceed(
                f"input size limit exceeded while reading '{source}'",
                self.budget.max_input_bytes,
                self.input_bytes,
            )

    def check_input_file(self, path: Union[str, Path]) -> None:
        """Check a file's size against the budget before it is read."""
        self.add_input_bytes(Path(path).stat().st_size, path)

    @contextmanager
    def layer(self, name: Optional[str]) -> Iterator[None]:
        """Track entering/leaving a layer, enforcing count and depth limits."""
        self.layer_count += 1
        self._layer_stack.append(name or "<unnamed>")
        try:
            if self.layer_count > self.budget.max_layers:
                self.exceed(
                    "layer count limit exceeded",
                    self.budget.max_layers,
                    self.layer_count,
                )
            if len(self._layer_stack) > self.budget.max_group_depth:
                self.exceed(
                    "layer nesting depth limit exceeded",
                    self.budget.max_group_depth,
                    len(self._layer_stack),
                )
            yield
        finally:
            self._layer_stack.pop()

    def add_objects(self, count: int) -> None:
        """Count objects in an object layer against the budget."""
        self.object_count += count
        if self.object_count > self.budget.max_objects:
            self.exceed(
                "object count limit exceeded",
                self.budget.max_objects,
                self.object_count,
            )

    def check_tile_count(self, count: int) -> None:
        """Check a (possibly running) per-layer tile count against the budget."""
        if count > self.budget.max_tiles_per_layer:
            self.exceed(
                "tile count limit exceeded for a single layer",
                self.budget.max_tiles_per_layer,
                count,
            )

    def check_dimensions(
        self, width: Optional[int], height: Optional[int], what: str = "layer"
    ) -> int:
        """Validate declared dimensions and return the claimed tile count.

        Raises MalformedMapError for missing or negative dimensions, and
        BudgetExceededError when the claimed tile count exceeds the budget.
        The multiplication is done before any allocation based on the
        dimensions, so absurdly large values (integer overflow territory)
        are rejected up front.
        """
        if width is None or height is None:
            self.malformed(f"{what} is missing required dimensions (width/height)")
        assert width is not None and height is not None  # for type checkers
        if width < 0 or height < 0:
            self.malformed(f"{what} has negative dimensions: {width}x{height}")
        tiles = width * height
        self.check_tile_count(tiles)
        return tiles

    def add_decompressed_bytes(self, count: int) -> None:
        """Count decompressed bytes against the budget."""
        self.decompressed_bytes += count
        if self.decompressed_bytes > self.budget.max_decompressed_bytes:
            self.exceed(
                "decompressed data size limit exceeded",
                self.budget.max_decompressed_bytes,
                self.decompressed_bytes,
            )


def decompress_bounded(
    tracker: BudgetTracker, data: bytes, compression: Optional[str]
) -> bytes:
    """Decompress data with a hard cap on the output size.

    For zlib and gzip the cap is enforced while decompressing, so a
    decompression bomb can never expand past the budget in memory. The
    decompressed size is counted against the tracker's
    max_decompressed_bytes budget either way, so uncompressed or
    unknown-compression data cannot bypass the limit.
    """
    if compression == "zlib" or compression == "gzip":
        wbits = zlib.MAX_WBITS if compression == "zlib" else 31
        decompressor = zlib.decompressobj(wbits)
        allowance = tracker.budget.max_decompressed_bytes - tracker.decompressed_bytes
        # Decompress in a loop, never asking for more than allowance + 1
        # bytes of output at a time. flush() is only called once all input
        # has been consumed under the allowance, so the bomb can never
        # expand past the budget in memory.
        parts = []
        total = 0
        buf = data
        while True:
            part = decompressor.decompress(buf, allowance + 1 - total)
            parts.append(part)
            total += len(part)
            if total > allowance:
                tracker.add_decompressed_bytes(total)
            if decompressor.unconsumed_tail:
                buf = decompressor.unconsumed_tail
                continue
            break
        parts.append(decompressor.flush())
        out = b"".join(parts)
        tracker.add_decompressed_bytes(len(out))
        return out
    elif compression == "zstd" and zstd is None:
        raise ValueError(
            "zstd compression support is not installed."
            "To install use 'pip install pytiled-parser[zstd]'"
        )
    # See note at top of module about zstd
    elif compression == "zstd":  # pragma: no cover
        out = zstd.decompress(data)
        tracker.add_decompressed_bytes(len(out))
        return out
    else:
        # No compression (or an unknown value, treated as none for backwards
        # compatibility). The bytes are still counted against the budget.
        tracker.add_decompressed_bytes(len(data))
        return data


def convert_raw_tile_layer_data(
    tracker: BudgetTracker, data: List[int], layer_width: Optional[int]
) -> List[List[int]]:
    """Convert raw layer data into a nested list based on the layer width.

    Args:
        tracker: The budget tracker for the current parse.
        data: The data to convert.
        layer_width: Width of the layer.

    Returns:
        List[List[int]]: A nested list containing the converted data.
    """
    if not isinstance(layer_width, int) or layer_width < 1:
        tracker.malformed(
            f"tile layer has an invalid width of {layer_width!r}; "
            "a positive width is required to interpret tile data"
        )
    assert isinstance(layer_width, int)  # for type checkers; malformed() raises

    tracker.check_tile_count(len(data))

    tile_grid: List[List[int]] = [[]]

    column_count = 0
    row_count = 0
    for item in data:
        column_count += 1
        tile_grid[row_count].append(item)
        if not column_count % layer_width and column_count < len(data):
            row_count += 1
            tile_grid.append([])

    return tile_grid


def decode_tile_layer_data(
    tracker: BudgetTracker,
    data: str,
    compression: Optional[str],
    layer_width: Optional[int],
) -> List[List[int]]:
    """Decode Base64 encoded tile data, with optional compression.

    Decompression is capped by the tracker's max_decompressed_bytes budget
    and the resulting tile count by max_tiles_per_layer.

    Args:
        tracker: The budget tracker for the current parse.
        data: The base64 encoded data.
        compression: Either zlib, gzip, zstd, or empty. If empty no
            decompression is done.
        layer_width: Width of the layer.

    Returns:
        List[List[int]]: A nested list containing the decoded data.

    Raises:
        ValueError: For an unsupported compression type.
    """
    unencoded_data = base64.b64decode(data)
    unzipped_data = decompress_bounded(tracker, unencoded_data, compression)

    # Reject based on the decoded size before materializing one Python int
    # per tile.
    tracker.check_tile_count(len(unzipped_data) // _BYTES_PER_TILE)

    tile_grid: List[int] = []

    byte_count = 0
    int_count = 0
    int_value = 0
    for byte in unzipped_data:
        int_value += byte << (byte_count * 8)
        byte_count += 1
        if not byte_count % _BYTES_PER_TILE:
            byte_count = 0
            int_count += 1
            tile_grid.append(int_value)
            int_value = 0

    return convert_raw_tile_layer_data(tracker, tile_grid, layer_width)
