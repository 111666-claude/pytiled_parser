"""Optional path sandbox for restricting recursive file references.

User-authored level packs can reference external tilesets, object templates
and images. A malicious or malformed pack can use ``../`` traversal, absolute
paths or in-pack symlinks to make the parser read files outside of the
directory the pack was extracted into.

The :class:`PathSandbox` provided here is opt-in: pass an instance to one of
the parsing functions (e.g. ``pytiled_parser.parse_map(file, sandbox=sandbox)``)
and every recursive reference is resolved to its real path and checked against
the allowed root directory(ies) before it is opened. When no sandbox is passed
the parser keeps its original, unrestricted behavior.
"""

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, List, Optional, Sequence, Union

PathLike = Union[str, os.PathLike]


@contextmanager
def _null_context() -> Iterator[None]:
    """A no-op context manager, equivalent to ``contextlib.nullcontext``.

    Provided explicitly (rather than via ``nullcontext``) so the package keeps
    working on its declared minimum of Python 3.6.
    """
    yield


class SandboxError(Exception):
    """Base class for all errors raised by the reference path sandbox.

    Attributes:
        reference: The raw reference string from the map/tileset/template,
            when one is available.
        resolved: The real path the reference resolved to, when available.
        root: The first configured sandbox root, for error messages.
    """

    def __init__(
        self,
        message: str,
        *,
        reference: Optional[str] = None,
        resolved: Optional[Path] = None,
        root: Optional[Path] = None,
    ) -> None:
        super().__init__(message)
        self.reference = reference
        self.resolved = resolved
        self.root = root


class SandboxAbsolutePath(SandboxError):
    """Raised when a reference uses an absolute path while a sandbox is active.

    Absolute references (POSIX ``/etc/passwd``, Windows ``C:\\...`` or UNC
    ``\\\\server\\share``) are rejected outright because they do not describe a
    location relative to the level pack.
    """


class SandboxPathOutsideRoot(SandboxError):
    """Raised when a reference resolves to a real path outside the roots.

    This covers both ``../`` traversal and symlinks that point outside of the
    allowed root directory(ies).
    """


class SandboxPathNotFound(SandboxError):
    """Raised when a referenced target does not exist."""


class SandboxCycle(SandboxError):
    """Raised when recursive references form a loop.

    For example an object template whose tileset source points back at the
    template itself, or a pair of templates referencing each other.
    """


def _looks_absolute(reference: str) -> bool:
    """Detect POSIX and Windows-style absolute paths on any platform.

    Both ``/`` and ``\\`` are treated as separators so that references crafted
    on another operating system are classified correctly.
    """
    normalized = reference.replace("\\", "/")
    if normalized.startswith("/"):
        # POSIX absolute path and Windows UNC paths (//server/share).
        return True
    # Windows drive-letter path, e.g. C:/foo or C:\foo
    return (
        len(normalized) >= 3
        and normalized[1] == ":"
        and normalized[2] == "/"
        and normalized[0].isalpha()
    )


class PathSandbox:
    """Restrict all file references to one or more allowed root directories.

    Roots are resolved to their real paths at construction time, so a symlinked
    root behaves predictably. Every reference is compared against the roots
    after symlink resolution, meaning an in-pack symlink pointing outside of a
    root is rejected even though its textual path stays inside the pack.

    Args:
        roots: A directory, or a sequence of directories, that references are
            allowed to resolve into.

    Raises:
        ValueError: If a given root does not exist or is not a directory.
    """

    def __init__(self, roots: Union[PathLike, Sequence[PathLike]]) -> None:
        if isinstance(roots, (str, os.PathLike)):
            roots = [roots]

        resolved_roots: List[Path] = []
        for root in roots:
            root_path = Path(os.fspath(root)).expanduser().absolute().resolve()
            if not root_path.exists():
                raise ValueError(f"Sandbox root does not exist: {root_path}")
            if not root_path.is_dir():
                raise ValueError(f"Sandbox root is not a directory: {root_path}")
            resolved_roots.append(root_path)

        self.roots: List[Path] = resolved_roots
        # Stack of real paths currently being parsed, for cycle detection.
        self._active: List[Path] = []

    def _is_within_roots(self, resolved_path: Path) -> bool:
        return any(
            resolved_path == root or root in resolved_path.parents
            for root in self.roots
        )

    def check_path(self, path: PathLike, *, context: str = "reference") -> Path:
        """Validate an existing path against the sandbox.

        Used for entry points (the map/tileset/world file handed directly to a
        parsing function). The path is resolved to its real path before being
        compared against the allowed roots.

        Args:
            path: The path to validate.
            context: Human-readable description of what is being checked, used
                to build distinguishable error messages.

        Returns:
            The resolved real path.

        Raises:
            SandboxPathNotFound: If the path does not exist.
            SandboxPathOutsideRoot: If its real path is outside of every root.
        """
        candidate = Path(os.fspath(path))
        resolved = candidate.absolute().resolve(strict=False)

        if not candidate.exists():
            raise SandboxPathNotFound(
                f"{context.capitalize()} target does not exist: {candidate}",
                resolved=resolved,
                root=self.roots[0],
            )

        if not self._is_within_roots(resolved):
            raise SandboxPathOutsideRoot(
                f"{context.capitalize()} resolves outside of the sandbox roots: "
                f"{candidate} -> {resolved}",
                resolved=resolved,
                root=self.roots[0],
            )

        return resolved

    @contextmanager
    def visiting(self, path: PathLike) -> Iterator[None]:
        """Context manager tracking which files are currently being parsed.

        Nested references resolved while the context is open are checked
        against the active stack, turning reference loops into a
        :class:`SandboxCycle` error instead of a confusing crash.
        """
        resolved = Path(os.fspath(path)).absolute().resolve(strict=False)
        if resolved in self._active:
            chain = " -> ".join(str(p) for p in self._active + [resolved])
            raise SandboxCycle(
                f"Circular reference detected while resolving {resolved}: {chain}",
                resolved=resolved,
                root=self.roots[0],
            )
        self._active.append(resolved)
        try:
            yield
        finally:
            self._active.pop()


def resolve_reference(
    sandbox: Optional[PathSandbox],
    base_dir: Path,
    reference: Union[str, PathLike],
    *,
    context: str = "reference",
) -> Path:
    """Resolve a Tiled file reference, enforcing the sandbox when one is given.

    When ``sandbox`` is ``None`` this is equivalent to the historical behavior
    of ``Path(base_dir) / reference`` and performs no checks, so default
    parsing is unchanged.

    When a sandbox is given, the reference is validated before anything opens
    it: absolute references, missing targets, symlink/``..`` escapes and
    reference cycles each raise a distinct, context-aware exception.

    Args:
        sandbox: The active :class:`PathSandbox`, or ``None``.
        base_dir: Directory the (relative) reference is interpreted against.
        reference: The raw reference string from the map/tileset/template.
        context: Human-readable description of what is being resolved (e.g.
            ``"tileset"`` or ``"object template"``), included in errors.

    Returns:
        The path to open. Under a sandbox this is the resolved real path.

    Raises:
        SandboxAbsolutePath: If ``reference`` is an absolute path.
        SandboxPathNotFound: If the referenced target does not exist.
        SandboxPathOutsideRoot: If its real path lies outside the roots.
    """
    ref_text = os.fspath(reference)

    if sandbox is None:
        return Path(base_dir) / ref_text

    if _looks_absolute(ref_text):
        raise SandboxAbsolutePath(
            f"{context.capitalize()} uses an absolute path, which is not "
            f"permitted by the sandbox: {ref_text}",
            reference=ref_text,
            root=sandbox.roots[0],
        )

    # Normalize mixed POSIX/Windows separators onto the host platform.
    normalized = ref_text.replace("\\", os.sep).replace("/", os.sep)
    candidate = (Path(base_dir) / normalized).absolute()
    resolved = candidate.resolve(strict=False)

    if not candidate.exists():
        raise SandboxPathNotFound(
            f"{context.capitalize()} target does not exist: {ref_text} "
            f"(resolved to {resolved})",
            reference=ref_text,
            resolved=resolved,
            root=sandbox.roots[0],
        )

    if not sandbox._is_within_roots(resolved):
        roots_text = ", ".join(str(root) for root in sandbox.roots)
        raise SandboxPathOutsideRoot(
            f"{context.capitalize()} reference escapes the sandbox: {ref_text} "
            f"resolves to {resolved}, which is outside of: {roots_text}",
            reference=ref_text,
            resolved=resolved,
            root=sandbox.roots[0],
        )

    return resolved
