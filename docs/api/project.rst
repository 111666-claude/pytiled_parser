.. _project_api:
Tiled Projects
==============

A Tiled project (``.tiled-project``) can define custom enum and custom class
types. Custom classes carry default values for their members, which Tiled uses
to pre-populate maps, layers, tiles, tilesets and objects assigned that class.

Project support is opt in. Pass ``project=True`` to
:func:`pytiled_parser.parse_map` or :func:`pytiled_parser.parse_tileset` to
auto-discover a single ``.tiled-project`` file in the map's or tileset's
directory. Alternatively pass a :class:`pathlib.Path` to a specific project
file, or an already loaded :class:`pytiled_parser.Project`.

When a project is applied, an object's *effective* properties are the result of
merging its class's member defaults with the values explicitly stored in the
map. Explicit values override same-named defaults, and nested custom class
properties are merged field by field rather than replaced wholesale. TMX and
JSON maps produce identical results.

Values keep the same types as ordinary properties. Colors are
:class:`pytiled_parser.Color`, file references are :class:`pathlib.Path`,
booleans stay booleans, object references are returned as
:class:`pytiled_parser.ObjectRef` (an ``int`` subclass holding the referenced
object's id) and nested classes are dictionaries of further properties.

pytiled_parser.Project
^^^^^^^^^^^^^^^^^^^^^^

.. autoclass:: pytiled_parser.Project
    :members:

pytiled_parser.ClassType
^^^^^^^^^^^^^^^^^^^^^^^^

.. autoclass:: pytiled_parser.ClassType
    :members:

pytiled_parser.ClassMember
^^^^^^^^^^^^^^^^^^^^^^^^^^

.. autoclass:: pytiled_parser.ClassMember
    :members:

pytiled_parser.EnumType
^^^^^^^^^^^^^^^^^^^^^^^

.. autoclass:: pytiled_parser.EnumType
    :members:

pytiled_parser.parse_project
^^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. autofunction:: pytiled_parser.parse_project

pytiled_parser.discover_project
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. autofunction:: pytiled_parser.discover_project

pytiled_parser.effective_properties
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. autofunction:: pytiled_parser.effective_properties

pytiled_parser.apply_project
^^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. autofunction:: pytiled_parser.apply_project

Errors
------

All project related errors derive from
:class:`pytiled_parser.ProjectError`, so a single ``except`` clause can catch
any of them. Every error message identifies the offending map, layer, tile,
tileset or object location.

.. autoclass:: pytiled_parser.ProjectNotFoundError
.. autoclass:: pytiled_parser.InvalidProjectError
.. autoclass:: pytiled_parser.UnknownClassError
.. autoclass:: pytiled_parser.CircularClassReferenceError
.. autoclass:: pytiled_parser.InvalidEnumValueError
.. autoclass:: pytiled_parser.PropertyTypeConflictError
.. autoclass:: pytiled_parser.InvalidPropertyPathError
