class UnknownFormat(Exception):
    pass


class ObjectTemplateError(Exception):
    """Raised when an object template file cannot be loaded.

    The error message always contains the path of the template file that
    could not be loaded.
    """
