"""Diagnostic helpers shared by the JSON and TMX parsers.

The parsers use the helpers in this module to report *where* a problem is,
instead of only *that* there is a problem.  The module keeps track of:

* the file currently being parsed,
* the field path inside that file,
* the layer/object currently being parsed (including group-layer nesting),
* the chain of object templates and referenced files.

Positions are collected lazily, and only when an error actually needs them, so
normal parsing stays as fast as before.
"""

import json
import os
import threading
import xml.etree.ElementTree as etree
from contextlib import contextmanager
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple, overload

from pytiled_parser.exception import (
    ExternalFileError,
    ExternalFileNotFoundError,
    FieldTypeError,
    FormatSyntaxError,
    InvalidFieldValueError,
    MissingFieldError,
    ParseError,
    UnrecognizedFormat,
    XMLSyntaxError,
)

try:  # pragma: no cover - always available on CPython
    import xml.parsers.expat as _expat
except ImportError:  # pragma: no cover
    _expat = None  # type: ignore[assignment]


PathSegment = Any
JsonPath = Tuple[PathSegment, ...]


def format_path(path: JsonPath) -> str:
    """Format a JSON path tuple the way users expect to see it.

    >>> format_path(("layers", 1, "objects", 3, "x"))
    'layers[1].objects[3].x'
    """
    result = ""
    for segment in path:
        if isinstance(segment, int):
            result += "[{}]".format(segment)
        elif result == "":
            result = str(segment)
        elif str(segment).isidentifier():
            result += ".{}".format(segment)
        else:
            result += "[{!r}]".format(segment)
    return result


def _join_field_prefix(base: str, suffix: Optional[str]) -> str:
    if not suffix:
        return base
    if not base:
        return suffix
    if suffix.startswith("["):
        return base + suffix
    return base + "." + suffix


def load_document(
    file: Any,
    *,
    display: Optional[str] = None,
    root_dir: Optional[Any] = None,
) -> "_Document":
    """Read *file* and return a :class:`JsonDocument` or :class:`XmlDocument`.

    ``root_dir`` is the directory of the map being parsed; when given,
    absolute paths are displayed relative to it so messages never expose
    unrelated filesystem layout.
    """

    display = display or display_file(file, root_dir)
    try:
        with open(file, "rb") as handle:
            data = handle.read()
    except FileNotFoundError as error:
        raise ExternalFileNotFoundError(
            "File not found: '{}'".format(display),
            file=display,
            reason="no such file: {}".format(display),
        ) from error
    except IsADirectoryError as error:
        raise ExternalFileError(
            "Expected a file but found a directory: '{}'".format(display),
            file=display,
            reason="path is a directory",
        ) from error
    except OSError as error:
        raise ExternalFileError(
            "Could not read '{}'".format(display),
            file=display,
            reason=str(error),
        ) from error

    prefix = data[:4096].decode("utf-8", "replace").lstrip("﻿ \t\r\n")
    if prefix.startswith("<"):
        return XmlDocument(file, data, display=display, root_dir=root_dir)
    if prefix.startswith(("{", "[")):
        return JsonDocument(
            file, data.decode("utf-8", "replace"), display=display, root_dir=root_dir
        )

    if not data.strip():
        message = "File '{}' is empty, expected a TMX or JSON map/tileset".format(
            display
        )
        reason = "file is empty"
    else:
        message = "Unknown format for '{}': expected a TMX/XML or JSON file".format(
            display
        )
        reason = "first character is neither '<' nor '{'"
    raise UnrecognizedFormat(message, file=display, reason=reason)


def display_file(file: Any, root_dir: Optional[Any] = None) -> str:
    """Return a display path for *file* which never leaks absolute paths.

    Absolute paths are shown relative to *root_dir* (usually the directory of
    the map being parsed), or as a bare file name when no useful relative path
    exists.  Relative paths — which the caller wrote themselves — are kept
    as-is.
    """

    if isinstance(file, str):
        path = file
    else:
        try:
            path = os.fspath(file)
        except TypeError:  # pragma: no cover - defensive
            return str(file)

    if not os.path.isabs(path):
        return os.path.normpath(path)

    if root_dir is not None:
        try:
            relative = os.path.relpath(path, os.fspath(root_dir))
            if not relative.startswith(os.pardir):
                return relative
        except (ValueError, OSError):  # pragma: no cover - different drives
            pass
        return os.path.basename(path)

    return os.path.basename(path)


def display_reference(reference: str, base_dir: Optional[Any] = None) -> str:
    """Return a safe display form of an external file reference.

    References are normally relative to the file which contains them; the
    important property is that we never emit an unrelated absolute path.
    """

    if not os.path.isabs(reference):
        return os.path.normpath(reference)
    if base_dir is not None:
        try:
            relative = os.path.relpath(reference, os.fspath(base_dir))
            if not relative.startswith(os.pardir):
                return relative
        except (ValueError, OSError):  # pragma: no cover
            pass
    return os.path.basename(reference)


class _LineIndex:
    """Maps character offsets to line/column pairs."""

    def __init__(self, text: str) -> None:
        self._newlines: List[int] = [
            index for index, char in enumerate(text) if char == "\n"
        ]

    def location(self, offset: int) -> Tuple[int, int]:
        import bisect

        line_index = bisect.bisect_left(self._newlines, offset)
        if line_index == 0:
            line_start = 0
        else:
            line_start = self._newlines[line_index - 1] + 1
        return line_index + 1, offset - line_start + 1


class _JsonPositionParser:
    """A tiny JSON scanner which records the position of every value.

    The scanner is only used for diagnostics.  The actual parsing is still
    done by :func:`json.loads`, so the values seen by the parsers are
    byte-for-byte what the standard library produces.
    """

    _WHITESPACE = " \t\r\n"
    _LITERALS = ("true", "false", "null")

    def __init__(self, text: str, line_index: _LineIndex) -> None:
        self.text = text
        self.length = len(text)
        self.index = 0
        self.line_index = line_index
        self.positions: Dict[JsonPath, Tuple[int, int, int]] = {}

    def parse(self) -> None:
        self._skip_whitespace()
        self._value(())
        self._skip_whitespace()
        if self.index != self.length:
            raise ValueError("trailing data")

    def _record(self, path: JsonPath, offset: int) -> None:
        line, column = self.line_index.location(offset)
        self.positions[path] = (line, column, offset)

    def _skip_whitespace(self) -> None:
        while self.index < self.length and self.text[self.index] in self._WHITESPACE:
            self.index += 1

    def _value(self, path: JsonPath) -> None:
        self._skip_whitespace()
        self._record(path, self.index)
        char = self.text[self.index]
        if char == "{":
            self._object(path)
        elif char == "[":
            self._array(path)
        elif char == '"':
            self._string()
        elif char in "-0123456789":
            self._number()
        else:
            literal = self.text[self.index : self.index + 5]
            matched = False
            for candidate in self._LITERALS:
                if literal.startswith(candidate):
                    self.index += len(candidate)
                    matched = True
                    break
            if not matched:
                raise ValueError("invalid JSON value")

    def _object(self, path: JsonPath) -> None:
        self.index += 1
        self._skip_whitespace()
        if self.index < self.length and self.text[self.index] == "}":
            self.index += 1
            return
        while True:
            self._skip_whitespace()
            key = self._string()
            self._skip_whitespace()
            if self.text[self.index] != ":":
                raise ValueError("expected ':'")
            self.index += 1
            self._value(path + (key,))
            self._skip_whitespace()
            if self.index >= self.length:
                raise ValueError("unterminated object")
            if self.text[self.index] == ",":
                self.index += 1
                continue
            if self.text[self.index] == "}":
                self.index += 1
                return
            raise ValueError("expected ',' or '}'")

    def _array(self, path: JsonPath) -> None:
        self.index += 1
        self._skip_whitespace()
        if self.index < self.length and self.text[self.index] == "]":
            self.index += 1
            return
        index = 0
        while True:
            self._value(path + (index,))
            index += 1
            self._skip_whitespace()
            if self.index >= self.length:
                raise ValueError("unterminated array")
            if self.text[self.index] == ",":
                self.index += 1
                continue
            if self.text[self.index] == "]":
                self.index += 1
                return
            raise ValueError("expected ',' or ']'")

    def _string(self) -> str:
        if self.text[self.index] != '"':
            raise ValueError("expected string")
        start = self.index
        self.index += 1
        while self.index < self.length:
            char = self.text[self.index]
            if char == "\\":
                self.index += 2
                continue
            if char == '"':
                self.index += 1
                token = self.text[start : self.index]
                try:
                    return json.loads(token)
                except ValueError:  # pragma: no cover - json.loads succeeded
                    return token[1:-1]
            self.index += 1
        raise ValueError("unterminated string")

    def _number(self) -> None:
        start = self.index
        if self.text[self.index] == "-":
            self.index += 1
        while self.index < self.length and (
            self.text[self.index].isdigit() or self.text[self.index] in ".eE+-"
        ):
            self.index += 1
        if self.index == start:
            raise ValueError("invalid number")


class _Document:
    """Base class for the format specific source documents."""

    format = ""

    def __init__(
        self, file: Any, display: Optional[str] = None, root_dir: Optional[Any] = None
    ) -> None:
        self.file = file
        self.root_dir = root_dir
        self.display_file = display or display_file(file, root_dir)

    def location(
        self,
        path: JsonPath = (),
        element: Optional[etree.Element] = None,
    ) -> Dict[str, Optional[int]]:
        return {"line": None, "column": None, "offset": None}


class JsonDocument(_Document):
    """A parsed JSON document which can resolve field paths to positions."""

    format = "json"

    def __init__(
        self,
        file: Any,
        text: str,
        display: Optional[str] = None,
        root_dir: Optional[Any] = None,
    ) -> None:
        super().__init__(file, display, root_dir)
        self.text = text
        self._line_index = _LineIndex(text)
        try:
            self.data = json.loads(text)
        except json.JSONDecodeError as error:
            line = error.lineno
            column = error.colno
            raise FormatSyntaxError(
                "Malformed JSON in '{}': {}".format(self.display_file, error.msg),
                file=self.display_file,
                line=line,
                column=column,
                offset=error.pos,
                reason=error.msg,
            ) from error
        self._positions: Optional[Dict[JsonPath, Tuple[int, int, int]]] = None

    def _ensure_positions(self) -> Dict[JsonPath, Tuple[int, int, int]]:
        if self._positions is None:
            parser = _JsonPositionParser(self.text, self._line_index)
            try:
                parser.parse()
            except (ValueError, IndexError):
                # json.loads already validated the document, so this should
                # not happen.  Degrade gracefully to "no positions".
                self._positions = {}
            else:
                self._positions = parser.positions
        return self._positions

    def location(
        self,
        path: JsonPath = (),
        element: Optional[etree.Element] = None,
    ) -> Dict[str, Optional[int]]:
        positions = self._ensure_positions()
        candidate = tuple(path)
        while candidate:
            if candidate in positions:
                line, column, offset = positions[candidate]
                return {"line": line, "column": column, "offset": offset}
            candidate = candidate[:-1]
        if () in positions:
            line, column, offset = positions[()]
            return {"line": line, "column": column, "offset": offset}
        return {"line": None, "column": None, "offset": None}


class XmlDocument(_Document):
    """A parsed XML document which maps elements to line/column positions."""

    format = "tmx"

    def __init__(
        self,
        file: Any,
        data: bytes,
        display: Optional[str] = None,
        root_dir: Optional[Any] = None,
    ) -> None:
        super().__init__(file, display, root_dir)
        self.data = data
        self._text = data.decode("utf-8", "replace")
        self._positions: Optional[Dict[int, Tuple[int, int, int]]] = None
        self._elements: List[etree.Element] = []
        try:
            self.root = etree.fromstring(data)
        except etree.ParseError as error:
            line, column = self._error_position(error)
            raise XMLSyntaxError(
                "Malformed XML in '{}': {}".format(self.display_file, error),
                file=self.display_file,
                line=line,
                column=column,
                offset=self._char_offset(line, column),
                reason=str(error),
            ) from error

    @staticmethod
    def _error_position(error: etree.ParseError) -> Tuple[Optional[int], Optional[int]]:
        position = getattr(error, "position", None)
        if not position:
            return None, None
        line, column = position
        return line, column + 1

    def _char_offset(self, line: Optional[int], column: Optional[int]) -> Optional[int]:
        if line is None or column is None:
            return None
        current_line = 1
        offset = 0
        for char in self._text:
            if current_line == line:
                return offset + column - 1
            offset += 1
            if char == "\n":
                current_line += 1
        return offset

    def _ensure_positions(self) -> Dict[int, Tuple[int, int, int]]:
        if self._positions is not None:
            return self._positions
        self._positions = {}
        if _expat is None:  # pragma: no cover
            return self._positions

        parser = _expat.ParserCreate()
        entries: List[Tuple[int, int, int]] = []

        def start_element(name: str, attributes: Dict[str, str]) -> None:
            entries.append(
                (
                    parser.CurrentLineNumber,
                    parser.CurrentColumnNumber + 1,
                    parser.CurrentByteIndex,
                )
            )

        parser.StartElementHandler = start_element
        try:
            parser.Parse(self.data, True)
        except etree.ParseError:  # pragma: no cover - malformed XML caught earlier
            return self._positions

        # ``id`` is stable for the lifetime of the tree as long as we keep a
        # reference to the root (which we do).
        self._position_list = entries
        self._elements = list(self.root.iter())
        for element, entry in zip(self._elements, entries):
            self._positions[id(element)] = entry
        return self._positions

    def location(
        self,
        path: JsonPath = (),
        element: Optional[etree.Element] = None,
    ) -> Dict[str, Optional[int]]:
        if element is None:
            return {"line": None, "column": None, "offset": None}
        positions = self._ensure_positions()
        entry = positions.get(id(element))
        if entry is None:
            return {"line": None, "column": None, "offset": None}
        line, column, offset = entry
        return {"line": line, "column": column, "offset": offset}


class _Frame:
    def __init__(
        self,
        document: Optional[_Document] = None,
        json_path: JsonPath = (),
        field_prefix: str = "",
        layer: Optional[str] = None,
        object: Optional[str] = None,
        template_chain: Tuple[str, ...] = (),
        template_files: Tuple[str, ...] = (),
        parent_dir: Optional[Any] = None,
        referenced_from: Optional[str] = None,
    ) -> None:
        self.document = document
        self.json_path = json_path
        self.field_prefix = field_prefix
        self.layer = layer
        self.object = object
        self.template_chain = template_chain
        self.template_files = template_files
        self.parent_dir = parent_dir
        self.referenced_from = referenced_from


_LOCAL = threading.local()


def _stack() -> List[_Frame]:
    stack = getattr(_LOCAL, "stack", None)
    if stack is None:
        stack = []
        _LOCAL.stack = stack
    return stack


def current_frame() -> _Frame:
    stack = _stack()
    if stack:
        return stack[-1]
    return _Frame()


_UNSET = object()


@contextmanager
def enter(
    document: Optional[_Document] = None,
    json_path: Optional[JsonPath] = None,
    field_prefix: Optional[str] = None,
    layer: Any = _UNSET,
    object: Any = _UNSET,
    template_chain: Optional[Iterable[str]] = None,
    template_files: Optional[Iterable[str]] = None,
    parent_dir: Any = _UNSET,
    referenced_from: Any = _UNSET,
) -> Iterator[_Frame]:
    """Push a new diagnostic context onto the stack."""

    base = current_frame()
    new_document = document if document is not None else base.document
    document_changed = document is not None and document is not base.document

    if json_path is None:
        json_path_value: JsonPath = () if document_changed else base.json_path
    else:
        json_path_value = (
            tuple(json_path) if document_changed else base.json_path + tuple(json_path)
        )

    if document_changed:
        prefix = field_prefix or ""
    else:
        prefix = _join_field_prefix(base.field_prefix, field_prefix)

    chain = tuple(template_chain) if template_chain is not None else base.template_chain
    template_files_value = (
        tuple(template_files) if template_files is not None else base.template_files
    )

    frame = _Frame(
        document=new_document,
        json_path=json_path_value,
        field_prefix=prefix,
        layer=base.layer if layer is _UNSET else layer,
        object=base.object if object is _UNSET else object,
        template_chain=chain,
        template_files=template_files_value,
        parent_dir=base.parent_dir if parent_dir is _UNSET else parent_dir,
        referenced_from=(
            base.referenced_from if referenced_from is _UNSET else referenced_from
        ),
    )
    stack = _stack()
    stack.append(frame)
    try:
        yield frame
    finally:
        stack.pop()


def _build_error(
    error_class: type,
    message: str,
    frame: _Frame,
    *,
    location: Dict[str, Optional[int]],
    file: Optional[str],
    field_path: Optional[str],
    reason: Optional[str],
) -> ParseError:
    return error_class(  # type: ignore[call-arg]
        message,
        file=file,
        line=location.get("line"),
        column=location.get("column"),
        offset=location.get("offset"),
        field_path=field_path,
        layer=frame.layer,
        object=frame.object,
        template_chain=(
            " -> ".join(frame.template_chain) if frame.template_chain else None
        ),
        reason=reason,
        referenced_from=frame.referenced_from,
    )


def json_error(
    error_class: type,
    message: str,
    *,
    path: JsonPath = (),
    reason: Optional[str] = None,
) -> ParseError:
    """Create an error using the current JSON diagnostic context."""

    frame = current_frame()
    document = frame.document if isinstance(frame.document, JsonDocument) else None
    full_path = frame.json_path + tuple(path)
    location: Dict[str, Optional[int]]
    if document is not None:
        location = document.location(full_path)
        file = document.display_file
    else:
        location = {"line": None, "column": None, "offset": None}
        file = None
    return _build_error(
        error_class,
        message,
        frame,
        location=location,
        file=file,
        field_path=format_path(full_path) if full_path else None,
        reason=reason,
    )


def xml_error(
    error_class: type,
    message: str,
    *,
    element: Optional[etree.Element] = None,
    field: Optional[str] = None,
    reason: Optional[str] = None,
) -> ParseError:
    """Create an error using the current XML diagnostic context."""

    frame = current_frame()
    document = frame.document if isinstance(frame.document, XmlDocument) else None
    location: Dict[str, Optional[int]]
    if element is None:
        location = {"line": None, "column": None, "offset": None}
    elif document is not None:
        location = document.location(element=element)
    else:
        location = {"line": None, "column": None, "offset": None}
    file = document.display_file if document is not None else None
    field_path = _join_field_prefix(frame.field_prefix, field) or None
    return _build_error(
        error_class,
        message,
        frame,
        location=location,
        file=file,
        field_path=field_path,
        reason=reason,
    )


_TYPE_NAMES = {
    str: "a string",
    bool: "a boolean",
    int: "an integer",
    float: "a number",
    list: "an array",
    dict: "an object",
}


def _type_name(expected: Any) -> str:
    if isinstance(expected, tuple):
        names = [_TYPE_NAMES.get(item, item.__name__) for item in expected]
        if len(names) == 1:
            return names[0]
        return " or ".join(names)
    return _TYPE_NAMES.get(expected, getattr(expected, "__name__", str(expected)))


def _matches_type(value: Any, expected: Any) -> bool:
    if isinstance(expected, tuple):
        return any(_matches_type(value, item) for item in expected)
    if expected is bool:
        return isinstance(value, bool)
    if expected is int:
        return isinstance(value, int) and not isinstance(value, bool)
    if expected is float:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, expected)


def full_json_path(path: JsonPath = ()) -> JsonPath:
    """Return *path* joined onto the current JSON context path."""
    return current_frame().json_path + tuple(path)


def check_type(value: Any, expected: Any, *, path: JsonPath, field: str = "") -> None:
    """Validate a JSON value against *expected*, raising a precise error."""

    if not _matches_type(value, expected):
        display_path = full_json_path(path)
        location = (
            "field '{}'".format(format_path(display_path)) if display_path else "value"
        )
        raise json_error(
            FieldTypeError,
            "Expected {} for {}, got {}: {!r}".format(
                _type_name(expected), location, type(value).__name__, value
            ),
            path=path,
            reason="expected {}, got {}".format(
                _type_name(expected), type(value).__name__
            ),
        )


def json_require(
    obj: Any,
    key: str,
    expected: Any,
    *,
    path: JsonPath = (),
    allow_none: bool = False,
) -> Any:
    """Return ``obj[key]`` after validating its presence and type."""

    if not isinstance(obj, dict):
        raise json_error(
            FieldTypeError,
            "Expected an object at '{}', got {}".format(
                format_path(full_json_path(path)) or "<root>", type(obj).__name__
            ),
            path=path,
            reason="expected an object, got {}".format(type(obj).__name__),
        )
    if key not in obj:
        raise json_error(
            MissingFieldError,
            "Missing required field '{}'".format(key),
            path=path + (key,),
            reason="required field is absent",
        )
    value = obj[key]
    if value is None and allow_none:
        return value
    check_type(value, expected, path=path + (key,))
    return value


def json_optional(
    obj: Any,
    key: str,
    expected: Any,
    *,
    path: JsonPath = (),
    default: Any = None,
) -> Any:
    """Return ``obj.get(key)``, validating the type when present."""

    if not isinstance(obj, dict):  # pragma: no cover - callers validate first
        raise json_error(
            FieldTypeError,
            "Expected an object at '{}', got {}".format(
                format_path(full_json_path(path)) or "<root>", type(obj).__name__
            ),
            path=path,
            reason="expected an object, got {}".format(type(obj).__name__),
        )
    if key not in obj or obj[key] is None:
        return default
    value = obj[key]
    check_type(value, expected, path=path + (key,))
    return value


def _xml_field(path: Optional[str], name: str) -> str:
    return _join_field_prefix(path or "", name)


@overload
def xml_attr(
    element: etree.Element,
    name: str,
    *,
    required: bool = ...,
    default: None = ...,
    field: Optional[str] = ...,
) -> str: ...


@overload
def xml_attr(
    element: etree.Element,
    name: str,
    *,
    required: bool = ...,
    default: Optional[str] = ...,
    field: Optional[str] = ...,
) -> Optional[str]: ...


def xml_attr(
    element: etree.Element,
    name: str,
    *,
    required: bool = True,
    default: Optional[str] = None,
    field: Optional[str] = None,
) -> Optional[str]:
    if name not in element.attrib:
        if required:
            raise xml_error(
                MissingFieldError,
                "Missing required attribute '{}' on <{}>".format(name, element.tag),
                element=element,
                field=_xml_field(field, name),
                reason="required attribute is absent",
            )
        return default
    return element.attrib[name]


def _convert_number(
    element: etree.Element,
    name: str,
    value: str,
    converter: Any,
    expected: str,
    field: Optional[str] = None,
) -> Any:
    try:
        return converter(value)
    except (TypeError, ValueError) as error:
        raise xml_error(
            FieldTypeError,
            "Expected {} for attribute '{}' on <{}>, got {!r}".format(
                expected, name, element.tag, value
            ),
            element=element,
            field=_xml_field(field, name),
            reason=str(error),
        ) from error


@overload
def xml_int_attr(
    element: etree.Element,
    name: str,
    *,
    required: bool = ...,
    default: None = ...,
    field: Optional[str] = ...,
) -> int: ...


@overload
def xml_int_attr(
    element: etree.Element,
    name: str,
    *,
    required: bool = ...,
    default: Optional[int] = ...,
    field: Optional[str] = ...,
) -> Optional[int]: ...


def xml_int_attr(
    element: etree.Element,
    name: str,
    *,
    required: bool = True,
    default: Optional[int] = None,
    field: Optional[str] = None,
) -> Optional[int]:
    value = xml_attr(element, name, required=required, field=field)
    if value is None:
        return default
    return _convert_number(element, name, value, int, "an integer", field)


@overload
def xml_float_attr(
    element: etree.Element,
    name: str,
    *,
    required: bool = ...,
    default: None = ...,
    field: Optional[str] = ...,
) -> float: ...


@overload
def xml_float_attr(
    element: etree.Element,
    name: str,
    *,
    required: bool = ...,
    default: Optional[float] = ...,
    field: Optional[str] = ...,
) -> Optional[float]: ...


def xml_float_attr(
    element: etree.Element,
    name: str,
    *,
    required: bool = True,
    default: Optional[float] = None,
    field: Optional[str] = None,
) -> Optional[float]:
    value = xml_attr(element, name, required=required, field=field)
    if value is None:
        return default
    return _convert_number(element, name, value, float, "a number", field)


@overload
def xml_bool_attr(
    element: etree.Element,
    name: str,
    *,
    required: bool = ...,
    default: None = ...,
    field: Optional[str] = ...,
) -> bool: ...


@overload
def xml_bool_attr(
    element: etree.Element,
    name: str,
    *,
    required: bool = ...,
    default: Optional[bool] = ...,
    field: Optional[str] = ...,
) -> Optional[bool]: ...


def xml_bool_attr(
    element: etree.Element,
    name: str,
    *,
    required: bool = True,
    default: Optional[bool] = None,
    field: Optional[str] = None,
) -> Optional[bool]:
    value = xml_attr(element, name, required=required, field=field)
    if value is None:
        return default
    if value not in ("0", "1"):
        raise xml_error(
            FieldTypeError,
            "Expected '0' or '1' for attribute '{}' on <{}>, got {!r}".format(
                name, element.tag, value
            ),
            element=element,
            field=_xml_field(field, name),
            reason="boolean attributes must be 0 or 1",
        )
    return bool(int(value))


def xml_text(
    element: etree.Element,
    *,
    required: bool = True,
    field: Optional[str] = None,
) -> Optional[str]:
    if element.text is None or element.text.strip() == "":
        if required:
            raise xml_error(
                MissingFieldError,
                "Missing required text content in <{}>".format(element.tag),
                element=element,
                field=field,
                reason="element has no text content",
            )
        return None
    return element.text


def xml_int_list(element: etree.Element, *, field: Optional[str] = None) -> List[int]:
    value = xml_text(element, field=field)
    assert value is not None
    result = []
    for position, item in enumerate(value.split(",")):
        try:
            result.append(int(item.strip()))
        except (TypeError, ValueError) as error:
            raise xml_error(
                InvalidFieldValueError,
                "Invalid integer {!r} at index {} in <{}>".format(
                    item.strip(), position, element.tag
                ),
                element=element,
                field=field,
                reason=str(error),
            ) from error
    return result


def describe_layer(raw: Any, index: Optional[int] = None) -> str:
    """Build a human readable identifier for a raw JSON layer."""

    if isinstance(raw, dict):
        name = raw.get("name")
        layer_id = raw.get("id")
        if name:
            return "'{}'".format(name)
        if layer_id is not None:
            return "<unnamed id={}>".format(layer_id)
    if index is not None:
        return "<layer index {}>".format(index)
    return "<layer>"


def describe_object(raw: Any, index: Optional[int] = None) -> str:
    """Build a human readable identifier for a raw JSON object."""

    if isinstance(raw, dict):
        object_id = raw.get("id")
        name = raw.get("name")
        if name and object_id is not None:
            return "id={} name='{}'".format(object_id, name)
        if object_id is not None:
            return "id={}".format(object_id)
        if name:
            return "name='{}'".format(name)
    if index is not None:
        return "<object index {}>".format(index)
    return "<object>"


def describe_xml_layer(element: etree.Element) -> str:
    name = element.attrib.get("name")
    layer_id = element.attrib.get("id")
    if name:
        return "'{}'".format(name)
    if layer_id is not None:
        return "<unnamed id={}>".format(layer_id)
    return "<{}>".format(element.tag)


def describe_xml_object(element: etree.Element) -> str:
    object_id = element.attrib.get("id")
    name = element.attrib.get("name")
    if name and object_id is not None:
        return "id={} name='{}'".format(object_id, name)
    if object_id is not None:
        return "id={}".format(object_id)
    if name:
        return "name='{}'".format(name)
    return "<{}>".format(element.tag)


def chain_layer(parent: Optional[str], child: str) -> str:
    if parent:
        return "{} > {}".format(parent, child)
    return child


def check_external_file(
    path: Any,
    reference: str,
    *,
    base_dir: Optional[Any] = None,
    json_path: JsonPath = (),
    element: Optional[etree.Element] = None,
    field: Optional[str] = None,
) -> None:
    """Raise a precise error when *path* is missing or unreadable."""

    display = display_reference(reference, base_dir)
    if not os.path.exists(path):
        error = (
            xml_error(
                ExternalFileNotFoundError,
                "External file not found: '{}'".format(display),
                element=element,
                field=field,
                reason="no such file: {}".format(display),
            )
            if element is not None
            else json_error(
                ExternalFileNotFoundError,
                "External file not found: '{}'".format(display),
                path=json_path,
                reason="no such file: {}".format(display),
            )
        )
        raise error
    if not os.path.isfile(path):
        error = (
            xml_error(
                ExternalFileError,
                "External file is not a regular file: '{}'".format(display),
                element=element,
                field=field,
                reason="not a regular file: {}".format(display),
            )
            if element is not None
            else json_error(
                ExternalFileError,
                "External file is not a regular file: '{}'".format(display),
                path=json_path,
                reason="not a regular file: {}".format(display),
            )
        )
        raise error
