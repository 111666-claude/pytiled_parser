"""Exceptions raised by pytiled_parser."""

from pathlib import Path
from typing import Optional, Union


class UnknownFormat(Exception):
    pass


class MapParseError(Exception):
    """Base class for parse failures that carry file and layer context.

    Attributes:
        file: The file being parsed when the error occurred, if known.
        layer: The position of the layer being parsed when the error occurred,
            if known. Nested group layers are joined with '/', e.g.
            ``"background/foreground"``.
    """

    def __init__(
        self,
        message: str,
        file: Optional[Union[str, Path]] = None,
        layer: Optional[str] = None,
    ) -> None:
        self.file = file
        self.layer = layer

        context = []
        if file is not None:
            context.append(f"file: {file}")
        if layer:
            context.append(f"layer: {layer}")
        if context:
            message = f"{message} ({', '.join(context)})"

        super().__init__(message)


class BudgetExceededError(MapParseError):
    """Raised when a parse budget limit is exceeded.

    Attributes:
        limit: The configured limit that was exceeded.
        actual: The value that exceeded the limit.
    """

    def __init__(
        self,
        message: str,
        file: Optional[Union[str, Path]] = None,
        layer: Optional[str] = None,
        limit: Optional[int] = None,
        actual: Optional[int] = None,
    ) -> None:
        self.limit = limit
        self.actual = actual
        super().__init__(message, file=file, layer=layer)


class MalformedMapError(MapParseError):
    """Raised when a map file is structurally invalid.

    For example: missing or negative layer dimensions, or non-integer
    dimension attributes.
    """
