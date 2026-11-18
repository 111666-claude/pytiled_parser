class UnknownFormat(Exception):
    pass


class ProjectError(Exception):
    """Base class for errors relating to a Tiled project (``.tiled-project``).

    All errors raised while loading a project or while resolving a map's
    effective properties against the custom classes/enums of a project derive
    from this class, so callers can catch any project related failure with a
    single ``except ProjectError``.
    """


class ProjectNotFoundError(ProjectError):
    """Raised when project support is requested but no project file exists."""


class InvalidProjectError(ProjectError):
    """Raised when a ``.tiled-project`` file can not be parsed or is malformed."""


class UnknownClassError(ProjectError):
    """Raised when an object references a custom class missing from the project."""


class CircularClassReferenceError(ProjectError):
    """Raised when custom class definitions reference each other in a cycle."""


class InvalidEnumValueError(ProjectError):
    """Raised when an enum property holds a value that is not part of the enum."""


class PropertyTypeConflictError(ProjectError):
    """Raised when an explicit property value does not match its declared type."""


class InvalidPropertyPathError(ProjectError):
    """Raised when a ``file`` property points at a path that does not exist."""
