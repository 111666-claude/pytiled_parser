.. _dependencies_api:
Dependencies
============

Parsing a map reads the map file, the external tilesets it references and the
object templates its objects use. It never looks at the images or files those
structures point at, so a map can parse successfully while still referencing
files that must be shipped with it. Packaging a level means copying every one
of those files, and missing even one produces a map which only fails at
runtime.

This module answers the question "which files does this map need?".
[collect_dependencies][pytiled_parser.dependencies.collect_dependencies] walks
the map, every external tileset and every object template recursively and
returns a [DependencyManifest][pytiled_parser.dependencies.DependencyManifest]
listing each referenced file once.

Relative paths are resolved against the directory of the file that contains
the reference, exactly like the parser resolves them. A file referenced from
several places is listed once, and keeps every reference in its ``sources``
list, so a shared sprite sheet can be traced back to each tileset, tile and
layer that uses it. Both the JSON and the TMX format of every kind of file
are understood.

The collected resource types are:

* ``tileset`` -- an external tileset (TSX or JSON).
* ``template`` -- an object template (TX or JSON).
* ``tileset_image`` -- the sprite sheet of a tileset.
* ``tile_image`` -- the image of an individual tile.
* ``image_layer_image`` -- the image of an image layer.
* ``file_property`` -- a custom property of type ``file``.

Nothing in the map objects is modified, and a parsed map is not required: the
manifest is built from the files themselves.

Functions
^^^^^^^^^

.. autofunction:: pytiled_parser.dependencies.collect_dependencies

.. autofunction:: pytiled_parser.dependencies.check_dependencies

Classes
^^^^^^^

.. autoclass:: pytiled_parser.dependencies.DependencyManifest
    :members:

.. autoclass:: pytiled_parser.dependencies.MapDependency
    :members:

.. autoclass:: pytiled_parser.dependencies.DependencySource
    :members:

.. autoclass:: pytiled_parser.dependencies.DependencyIssue
    :members:

.. autoclass:: pytiled_parser.dependencies.ResourceType
    :members:

.. autoclass:: pytiled_parser.dependencies.IssueCode
    :members:
