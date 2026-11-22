.. _exceptions_api:
Errors and Diagnostics
=======================

Every parse error raised by PyTiled Parser carries structured diagnostic
information: the file, the line/column or byte offset where available, the
field path, and the layer / object / template chain being parsed.

The exceptions all derive from :class:`pytiled_parser.ParseError`, and each
also derives from the builtin exception type that older versions raised, so
existing ``except`` clauses keep working.

Example::

    from pathlib import Path
    from pytiled_parser import parse_map, ParseError

    try:
        parse_map(Path("map.json"))
    except ParseError as error:
        print(error)
        print(error.to_dict())

A rendered error looks like::

    Expected an integer for property 'hp', got str: 'many'
      file: map.json
      location: line 10, column 22 (offset 378)
      layer: 'Object Layer 1'
      object: id=2 name='crate'
      field: layers[0].objects[0].properties[0].value
      reason: expected an integer, got str

Distinguishing unknown formats from data errors
-----------------------------------------------

:class:`pytiled_parser.UnknownFormat` is still raised for every file that the
parser cannot handle, so old code that catches it continues to work.  The
concrete subclass says *why*:

* :class:`pytiled_parser.UnrecognizedFormat` — the file is neither XML/TMX nor
  JSON.  This is a genuinely unknown format.
* :class:`pytiled_parser.FormatSyntaxError` (and its XML-specific subclass
  :class:`pytiled_parser.XMLSyntaxError`) — the file is a recognized format
  but is syntactically malformed.  These are also ``ValueError`` (and
  ``xml.etree.ElementTree.ParseError`` for XML), matching what the underlying
  parsers raise.

Data errors inside a recognized format never masquerade as ``UnknownFormat``;
they use one of :class:`pytiled_parser.MissingFieldError`,
:class:`pytiled_parser.FieldTypeError`,
:class:`pytiled_parser.InvalidFieldValueError`,
:class:`pytiled_parser.UnknownLayerTypeError`,
:class:`pytiled_parser.ExternalFileNotFoundError` (also ``FileNotFoundError``),
or :class:`pytiled_parser.CircularReferenceError`.

Object templates
----------------

Nested templates are resolved recursively.  Cycles are detected and reported
with the full chain, for example ``cycle_a.json -> cycle_b.json -> cycle_a.json``.
Errors inside a template point at the template file and use paths relative to
it, never at unrelated filesystem paths.

API
---

.. autoclass:: pytiled_parser.ParseError
    :members: to_dict

.. autoclass:: pytiled_parser.UnknownFormat

.. autoclass:: pytiled_parser.UnrecognizedFormat

.. autoclass:: pytiled_parser.FormatSyntaxError

.. autoclass:: pytiled_parser.XMLSyntaxError

.. autoclass:: pytiled_parser.MissingFieldError

.. autoclass:: pytiled_parser.FieldTypeError

.. autoclass:: pytiled_parser.InvalidFieldValueError

.. autoclass:: pytiled_parser.ExternalFileError

.. autoclass:: pytiled_parser.ExternalFileNotFoundError

.. autoclass:: pytiled_parser.UnknownLayerTypeError

.. autoclass:: pytiled_parser.TemplateError

.. autoclass:: pytiled_parser.CircularReferenceError

.. autoclass:: pytiled_parser.UnsupportedTemplateError

.. autoclass:: pytiled_parser.UnsupportedCompressionError
