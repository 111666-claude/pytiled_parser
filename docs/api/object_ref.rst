.. _object_ref_api:
Object References
=================

This module provides resolution of ``object`` type custom properties.

In Tiled, an ``object`` type property stores the ID of another object in the same map.
After parsing, these properties are ``pytiled_parser.ObjectRef`` instances which preserve
the raw value from the map file. ``resolve_object_references`` turns them into the actual
objects they point to, covering properties on the map, layers, objects, tilesets, and tiles.

.. automodule:: pytiled_parser.object_ref
    :members:
    :undoc-members:
    :show-inheritance:
