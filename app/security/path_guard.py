from pathlib import Path

from app.security.capabilities import (
    CapabilityMatrix,
    DELETE,
    READ,
    WRITE,
)

# Backwards-compatible aliases for the old two-mode API.
_LEGACY_MODES = {
    "read": READ,
    "write": WRITE,
    "delete": DELETE,
}


class PathGuard:
    """
    Filesystem authorization.

    All decisions come from CapabilityMatrix; this class only resolves the
    incoming string and shapes the answer the tools expect.
    """

    def __init__(self, matrix: CapabilityMatrix | None = None):
        self.matrix = matrix or CapabilityMatrix()
        self.home = Path.home().resolve()

    @property
    def allowed_roots(self) -> list[Path]:
        return [
            zone.path
            for zone in self.matrix.zones
            if zone.capabilities
        ]

    def validate(
        self,
        path: str,
        mode: str = "read",
        item: bool = False,
    ):
        """
        mode -- a capability name (read, write, copy, move, duplicate,
                delete).
        item -- True when `path` is the individual file or folder being
                modified, rather than the folder being worked inside.
        """

        if not isinstance(path, str):
            return {
                "allowed": False,
                "error": "Path must be a string.",
            }

        path = path.strip()

        if not path:
            return {
                "allowed": False,
                "error": "Path cannot be empty.",
            }

        try:
            resolved = Path(path).expanduser().resolve()

        except (OSError, RuntimeError) as error:
            return {
                "allowed": False,
                "error": f"Invalid path: {error}",
            }

        capability = _LEGACY_MODES.get(mode, mode)

        verdict = (
            self.matrix.check_item(resolved, capability)
            if item
            else self.matrix.check(resolved, capability)
        )

        if not verdict.allowed:
            return {
                "allowed": False,
                "error": verdict.error,
            }

        return {
            "allowed": True,
            "path": str(resolved),
            "zone": verdict.zone.label if verdict.zone else "",
        }
