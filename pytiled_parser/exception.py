"""Exceptions raised by :mod:`pytiled_parser`.

Every parser error carries structured diagnostic information in addition to a
human readable message:

``file``
    The source file the error was found in, as a display path (never an
    absolute path resolved from the current working directory).
``line`` / ``column`` / ``offset``
    Location in the source file, when the format makes it available.
``field_path``
    Path to the offending field, for example
    ``layers[1].objects[3].properties[2].value``.
``layer`` / ``object``
    Identifier of the layer / object being parsed, including the chain of
    enclosing group layers.
``template_chain``
    Chain of object templates leading to the error, for nested templates.
``reason``
    A short description of the underlying cause, when there is one.

The hierarchy is designed to keep backwards compatibility.  For example
:class:`UnknownFormat` is still raised for files which cannot be identified as
either TMX or JSON, syntax errors in those formats remain catchable as
``ValueError``/``xml.etree.ElementTree.ParseError``, missing external files
remain catchable as ``OSError``/``FileNotFoundError`` and so on.
"""

import xml.etree.ElementTree as etree
from typing import Any, Dict, List, Optional


class ParseError(Exception):
    """Base class for all errors raised while parsing Tiled data.

    The class is intentionally a plain :class:`Exception` subclass so that
    existing code which catches broad ``ValueError``/``KeyError``/etc. from
    individual helpers keeps working through the more specific subclasses
    below.
    """

    def __init__(
        self,
        message: str = "",
        *,
        file: Optional[str] = None,
        line: Optional[int] = None,
        column: Optional[int] = None,
        offset: Optional[int] = None,
        field_path: Optional[str] = None,
        layer: Optional[str] = None,
        object: Optional[str] = None,
        template_chain: Optional[str] = None,
        reason: Optional[str] = None,
        referenced_from: Optional[str] = None,
    ) -> None:
        self.message = message
        self.file = file
        self.line = line
        self.column = column
        self.offset = offset
        self.field_path = field_path
        self.layer = layer
        self.object = object
        self.template_chain = template_chain
        self.reason = reason
        #: Where an external file was referenced from, e.g.
        #: ``"map.json: tilesets[0].source"``.  Set when the error is inside a
        #: referenced file, so the referencing location is not lost.
        self.referenced_from = referenced_from
        # ``Exception`` (and some of the builtins we mix in) take arguments
        # differently.  Passing a single message keeps ``args`` predictable.
        super().__init__(message)

    def __str__(self) -> str:
        details: List[str] = []

        if self.file is not None:
            details.append("file: {}".format(self.file))

        if self.line is not None:
            location = "line {}".format(self.line)
            if self.column is not None:
                location += ", column {}".format(self.column)
            if self.offset is not None:
                location += " (offset {})".format(self.offset)
            details.append("location: {}".format(location))
        elif self.offset is not None:
            details.append("location: offset {}".format(self.offset))

        if self.layer:
            details.append("layer: {}".format(self.layer))

        if self.object:
            details.append("object: {}".format(self.object))

        if self.field_path:
            details.append("field: {}".format(self.field_path))

        if self.template_chain:
            details.append("template chain: {}".format(self.template_chain))

        if self.referenced_from:
            details.append("referenced from: {}".format(self.referenced_from))

        if self.reason:
            details.append("reason: {}".format(self.reason))

        cause = self.__cause__
        if cause is not None and not self.reason:
            details.append("reason: {}: {}".format(type(cause).__name__, str(cause)))

        if not details:
            return self.message

        return "{}\n  {}".format(self.message, "\n  ".join(details))

    def to_dict(self) -> Dict[str, Any]:
        """Return the diagnostic information as a plain dictionary."""

        return {
            "type": type(self).__name__,
            "message": self.message,
            "file": self.file,
            "line": self.line,
            "column": self.column,
            "offset": self.offset,
            "field_path": self.field_path,
            "layer": self.layer,
            "object": self.object,
            "template_chain": self.template_chain,
            "reason": self.reason,
            "referenced_from": self.referenced_from,
        }


class UnknownFormat(ParseError):
    """Base class for errors indicating a file could not be understood.

    .. note::

        This is retained as the public exception users have always been able
        to catch.  Use :class:`UnrecognizedFormat` to specifically detect a
        file whose format could not be identified at all, and
        :class:`FormatSyntaxError` for malformed content in an otherwise
        recognized format.
    """


class UnrecognizedFormat(UnknownFormat):
    """The file is neither XML/TMX nor JSON.

    This is a *genuinely* unknown format.  Data errors inside a recognized
    format never use this class.
    """


class FormatSyntaxError(UnknownFormat, ValueError):
    """The file looks like JSON/XML but is syntactically malformed.

    Catchable as :class:`UnknownFormat` (historical behaviour) and as
    ``ValueError`` (the type ``json`` raises).
    """


class XMLSyntaxError(FormatSyntaxError, etree.ParseError):
    """Malformed XML/TMX content.

    Catchable as :class:`FormatSyntaxError`, :class:`UnknownFormat`,
    ``ValueError`` and ``xml.etree.ElementTree.ParseError``.
    """


class MissingFieldError(ParseError, KeyError):
    """A required field is missing from the data."""


class FieldTypeError(ParseError, TypeError, ValueError):
    """A field has the wrong JSON type.

    Catchable as ``TypeError`` and ``ValueError`` for compatibility with the
    exceptions previously produced by indexing/conversion code.
    """


class InvalidFieldValueError(ParseError, ValueError):
    """A field has the right type but an invalid value."""


class ExternalFileError(ParseError, OSError):
    """An external file referenced by a map/tileset/world could not be read."""


class ExternalFileNotFoundError(ExternalFileError, FileNotFoundError):
    """An external file referenced by a map/tileset/world does not exist."""


class UnknownLayerTypeError(ParseError, RuntimeError):
    """A layer declares a type the parser does not understand."""


class CircularReferenceError(ParseError, RuntimeError):
    """Object templates reference each other in a cycle."""


class UnsupportedTemplateError(ParseError, NotImplementedError):
    """Cross-format template inheritance is not supported."""


class TemplateError(ParseError, RuntimeError):
    """An object template could not be resolved."""


class UnsupportedCompressionError(ParseError, ValueError):
    """Tile data uses an unsupported/invalid compression scheme."""


__all__ = [
    "CircularReferenceError",
    "ExternalFileError",
    "ExternalFileNotFoundError",
    "FieldTypeError",
    "FormatSyntaxError",
    "InvalidFieldValueError",
    "MissingFieldError",
    "ParseError",
    "TemplateError",
    "UnknownFormat",
    "UnknownLayerTypeError",
    "UnrecognizedFormat",
    "UnsupportedCompressionError",
    "UnsupportedTemplateError",
    "XMLSyntaxError",
]
