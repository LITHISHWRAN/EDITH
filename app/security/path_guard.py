from pathlib import Path
import os


class PathGuard:
    """
    Controls which filesystem paths EDITH is allowed to access.

    Phase 3:
    - Allow access to the user's home directory.
    - Allow access to common user folders.
    - Reject Windows system directories.
    - Reject path traversal outside the allowed roots.
    """

    def __init__(self):

        self.home = Path.home().resolve()

        self.allowed_roots = [
            self.home / "Desktop",
            self.home / "Documents",
            self.home / "Downloads",
            self.home / "Pictures",
            self.home / "Videos",
            self.home / "Music",
            self.home / "Projects",
        ]

        # Home itself is also allowed.
        self.allowed_roots.append(self.home)

        self.blocked_roots = [
            Path(os.environ.get(
                "WINDIR",
                r"C:\Windows"
            )).resolve(),

            Path(os.environ.get(
                "PROGRAMFILES",
                r"C:\Program Files"
            )).resolve(),

            Path(os.environ.get(
                "PROGRAMFILES(X86)",
                r"C:\Program Files (x86)"
            )).resolve(),
        ]

    def validate(self, path: str):

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

        except (OSError, RuntimeError) as e:

            return {
                "allowed": False,
                "error": f"Invalid path: {e}",
            }

        # -----------------------------------------
        # Block Windows system locations
        # -----------------------------------------

        for blocked in self.blocked_roots:

            if self._is_inside(
                resolved,
                blocked
            ):
                return {
                    "allowed": False,
                    "error": (
                        f"Access to protected system "
                        f"path is denied: {resolved}"
                    ),
                }

        # -----------------------------------------
        # Check allowed locations
        # -----------------------------------------

        for allowed in self.allowed_roots:

            if self._is_inside(
                resolved,
                allowed.resolve()
            ):
                return {
                    "allowed": True,
                    "path": str(resolved),
                }

        return {
            "allowed": False,
            "error": (
                f"Access to this path is not allowed: "
                f"{resolved}"
            ),
        }

    @staticmethod
    def _is_inside(
        path: Path,
        root: Path,
    ) -> bool:

        try:
            path.relative_to(root)
            return True

        except ValueError:
            return False