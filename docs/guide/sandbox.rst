Reference Path Sandbox
======================

.. default-role:: code

When players or mod authors distribute their own level packs, the maps inside
can reference external tilesets, object templates and images. Normally
PyTiled Parser follows those references wherever they point, including
``../`` traversal, absolute paths and symlinks. If a level pack is untrusted,
that can be used to make your application read files outside of the directory
the pack was extracted into.

The optional sandbox restricts every recursive reference to one or more
allowed root directories. It is opt-in: parsing behavior is unchanged unless
you explicitly create a `PathSandbox`.

Enabling the sandbox
--------------------

Create a `~pytiled_parser.PathSandbox` rooted at the directory the level pack
was extracted into, and pass it to one of the parsing functions:

.. code-block:: python

    from pathlib import Path

    from pytiled_parser import PathSandbox, parse_map

    sandbox = PathSandbox(Path("/opt/game/user_levels/cool_pack"))
    tiled_map = parse_map(
        Path("/opt/game/user_levels/cool_pack/map.json"),
        sandbox=sandbox,
    )

The same argument is accepted by `parse_map`, `parse_tileset` and
`parse_world`. Multiple roots are allowed by passing a list:

.. code-block:: python

    sandbox = PathSandbox([pack_dir, shared_asset_dir])

Roots are resolved to their real paths when the sandbox is constructed, so a
symlinked root behaves predictably.

What gets checked
-----------------

Before each referenced file is opened, its *real* path (after symlink
resolution) is compared against the sandbox roots. The checks apply
recursively to:

* external tilesets referenced by a map,
* object templates and the tilesets they reference,
* tileset images, including per-tile images,
* image layers,
* the maps listed by a world file.

Because the comparison uses the real path, an in-pack symlink that points at
a file outside of a root is rejected even though its textual path stays inside
the pack.

References using mixed ``/`` and ``\\`` separators are normalized before
checking, and Windows-style drive (``C:\\...``) and UNC (``\\\\server\\share``)
paths are recognized and rejected on any platform.

Distinguishable errors
----------------------

Each failure mode raises a distinct exception so calling code can react
differently:

* `~pytiled_parser.SandboxAbsolutePath` — an absolute reference was used.
* `~pytiled_parser.SandboxPathOutsideRoot` — a relative reference or symlink
  resolves outside of every allowed root.
* `~pytiled_parser.SandboxPathNotFound` — the referenced target does not
  exist.
* `~pytiled_parser.SandboxCycle` — recursive references form a loop, for
  example a template whose tileset source points back at itself.

All four inherit from `~pytiled_parser.SandboxError` if you want a single
catch-all. Each exception carries the raw `reference` string (when available),
the `resolved` real path and the configured `root` for logging.

.. code-block:: python

    from pytiled_parser import (
        SandboxAbsolutePath,
        SandboxPathNotFound,
        SandboxPathOutsideRoot,
        parse_map,
    )

    try:
        tiled_map = parse_map(map_path, sandbox=sandbox)
    except SandboxAbsolutePath as error:
        reject_pack(f"Absolute paths are not allowed: {error.reference}")
    except SandboxPathOutsideRoot as error:
        reject_pack(f"Reference escapes the level pack: {error.reference}")
    except SandboxPathNotFound as error:
        reject_pack(f"Missing referenced file: {error.reference}")

Default behavior
----------------

When no sandbox is supplied, every function keeps its original behavior.
Same-directory, sub-directory and even ``../`` references that leave the pack
continue to resolve exactly as before, so existing code does not need to
change. Only callers that opt in receive the restrictions.
