class UnknownFormat(Exception):
    pass


class CyclicLayerGroupError(Exception):
    """Raised when a LayerGroup is reachable from itself during effective
    layer traversal.

    The layer tree produced by parsing a Tiled map is always acyclic, so this
    normally only happens with hand built or mutated layer objects.
    """


class InvalidTintColorError(Exception):
    """Raised when a layer's tint color can not be composed.

    A valid tint color is a
    [Color][pytiled_parser.common_types.Color] whose four channels all lie
    between 0 and 255 inclusive.
    """


class UnsupportedOrientationError(Exception):
    """Raised when effective layer queries are used with a map orientation
    that does not map pixel coordinates to tile cells with a simple grid
    transform.

    Orthogonal maps are supported. Isometric, staggered and hexagonal maps
    raise this error because their pixel-to-cell math differs and is not
    handled yet.
    """
