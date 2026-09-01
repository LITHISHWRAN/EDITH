from pathlib import Path
import os
import re

# Explicit: 'D:', 'D:\', 'D:/', 'D drive', 'the d drive', 'D disk', 'D volume'
_DRIVE_REFERENCE = re.compile(
    r"^(?:the\s+|my\s+)?([a-z])\s*"
    r"(?::\s*(?:drive|disk|volume)?|\s+(?:drive|disk|volume))\s*[\\/]?$",
    re.IGNORECASE,
)

# Bare: just 'd'. Only a drive if that drive actually exists, so a stray
# single letter cannot silently become a filesystem root.
_BARE_DRIVE_LETTER = re.compile(
    r"^(?:the\s+|my\s+)?([a-z])\s*[\\/]?$",
    re.IGNORECASE,
)


def drive_reference(text: str) -> Path | None:
    """
    Interpret text as a whole drive, or return None.

    Handles 'D:', 'd drive', 'D disk' and a bare 'd'. The bare form is
    accepted only when the drive is mounted -- otherwise 'open d' would
    resolve to a root that does not exist instead of falling through to
    folder and app matching.
    """
    if not isinstance(text, str):
        return None

    candidate = text.strip()

    if not candidate:
        return None

    match = _DRIVE_REFERENCE.match(candidate)

    if match:
        return Path(f"{match.group(1).upper()}:\\")

    match = _BARE_DRIVE_LETTER.match(candidate)

    if match:
        root = Path(f"{match.group(1).upper()}:\\")

        if root.is_dir():
            return root

    return None


class PathResolver:
    """
    Resolves human-friendly Windows directory names
    to the actual directories configured for the
    current Windows user.
    """

    def __init__(self):

        self.home = Path.home()

        self.aliases = {
            "home": self.home,
            "desktop": self._known_folder("Desktop"),
            "documents": self._known_folder("MyDocuments"),
            "downloads": self.home / "Downloads",
            "pictures": self._known_folder("MyPictures"),
            "videos": self._known_folder("MyVideos"),
            "music": self._known_folder("MyMusic"),
        }

    def _known_folder(self, folder_name: str) -> Path:

        try:

            import ctypes

            buffer = ctypes.create_unicode_buffer(260)

            # CSIDL mappings
            csidl = {
                "Desktop": 0x0000,
                "MyDocuments": 0x0005,
                "MyPictures": 0x0027,
                "MyMusic": 0x000D,
                "MyVideos": 0x000E,
            }

            result = ctypes.windll.shell32.SHGetFolderPathW(
                None,
                csidl[folder_name],
                None,
                0,
                buffer,
            )

            if result == 0 and buffer.value:

                return Path(
                    buffer.value
                ).resolve()

        except Exception:
            pass

        # Fallback
        fallback = {
            "Desktop": self.home / "Desktop",
            "MyDocuments": self.home / "Documents",
            "MyPictures": self.home / "Pictures",
            "MyMusic": self.home / "Music",
            "MyVideos": self.home / "Videos",
        }

        return fallback[folder_name].resolve()

    def resolve(self, path: str, base: Path | None = None) -> Path:
        """
        base -- directory that bare relative names resolve against.
                Defaults to the user's home.

        A relative path is never resolved against the process working
        directory. EDITH's install directory is meaningless to the user, and
        silently pointing 'PythonCode' at D:\\EDITH\\PythonCode would operate
        on the wrong folder rather than failing.
        """

        if not isinstance(path, str):
            raise TypeError(
                "Path must be a string."
            )

        path = path.strip()

        if not path:
            raise ValueError(
                "Path cannot be empty."
            )

        normalized = path.lower()

        if normalized in self.aliases:

            return self.aliases[
                normalized
            ].resolve()

        # Whole-drive references. 'D:' is NOT an absolute path to pathlib --
        # it is drive-relative, and resolving it lands on the current
        # directory of that drive rather than its root.
        drive = drive_reference(path)

        if drive is not None:
            return drive

        # A logical name used as a prefix: 'Downloads/games',
        # 'Documents\\notes.txt'. The model produces these constantly, and
        # without this they would resolve against the process working
        # directory instead of the user's folders.
        segments = path.replace("\\", "/").split("/")

        if len(segments) > 1:

            head = segments[0].strip().lower()

            if head in self.aliases:

                tail = [
                    segment
                    for segment in segments[1:]
                    if segment.strip()
                ]

                return (
                    self.aliases[head]
                    .joinpath(*tail)
                    .resolve()
                )

        candidate = Path(path).expanduser()

        # 'D:work' carries a drive but no root, so it means "the current
        # directory on D:". Anchor it at the drive root instead -- process
        # state must never decide what a path means.
        if candidate.drive and not candidate.root:
            tail = str(candidate)[len(candidate.drive):].lstrip("\\/")

            return (Path(candidate.drive + "\\") / tail).resolve()

        if not candidate.is_absolute():
            candidate = (base or self.home) / candidate

        return candidate.resolve()

    def roots(self) -> list[Path]:
        """The real directories behind the logical names."""
        return [target.resolve() for target in self.aliases.values()]