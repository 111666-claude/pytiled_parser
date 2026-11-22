.. _sandbox_api:
Sandbox
=======

The optional reference path sandbox restricts the files a map, tileset,
template or world is allowed to open while recursive references are being
resolved.

pytiled_parser.PathSandbox
^^^^^^^^^^^^^^^^^^^^^^^^^^

.. autoclass:: pytiled_parser.PathSandbox
    :members:

pytiled_parser.resolve_reference
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. autofunction:: pytiled_parser.sandbox.resolve_reference

Exceptions
^^^^^^^^^^

.. autoexception:: pytiled_parser.SandboxError
    :members:

.. autoexception:: pytiled_parser.SandboxAbsolutePath
    :members:

.. autoexception:: pytiled_parser.SandboxPathOutsideRoot
    :members:

.. autoexception:: pytiled_parser.SandboxPathNotFound
    :members:

.. autoexception:: pytiled_parser.SandboxCycle
    :members:
