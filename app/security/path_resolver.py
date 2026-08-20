from pathlib import Path
import os


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

    def resolve(self, path: str) -> Path:

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

        return (
            Path(path)
            .expanduser()
            .resolve()
        )