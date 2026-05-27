"""Spatial and state predicates — ported verbatim from RoboReason-Lab."""


class Predicates:

    p2p_predicates = """
    - Left(a, b): Object a is to the left of object b
    - Right(a, b): Object a is to the right of object b
    - Front(a, b): Object a is in front of object b
    - Behind(a, b): Object a is behind object b
    - Above(a, b): Object a is above object b
    - Below(a, b): Object a is below object b
    - Contact(a, b): Object a is in contact with object b
    - Boundary(a, b): Object a is within the boundary of object b
    - Inside(a, b): Object a is inside object b
    - Blocking(a, b): Object a is physically blocking object b
    - Opened(a): Object a is opened
    - Closed(a): Object a is closed
    """

    more_predicates = """
    - Reachable(a, b): Object a is reachable by agent b
    - Unreachable(a): Object a is not reachable
    - NotBlocking(a, b): Object a is not blocking object b
    - Blocked(a): Object a is blocked by something
    - HasWarning(a): Object a has a warning
    - IsSafe(a): Object a is now safe
    """

    @staticmethod
    def get_all_predicates() -> str:
        return Predicates.p2p_predicates.strip() + "\n" + Predicates.more_predicates.strip()
