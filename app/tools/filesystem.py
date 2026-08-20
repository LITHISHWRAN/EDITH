from pathlib import Path

from app.tools.base import Tool
from app.security.path_guard import PathGuard
from app.security.path_resolver import PathResolver


class ListDirectoryTool(Tool):

    name = "list_directory"

    description = (
        "List the files and directories inside a Windows directory. "
        "Use this when the user asks what files or folders are present "
        "in a directory."
    )

    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": (
                    "Absolute path of the directory to inspect."
                ),
            }
        },
        "required": ["path"],
        "additionalProperties": False,
    }

    def __init__(self):

        self.path_guard = PathGuard()
        self.path_resolver = PathResolver()

    def execute(self, path: str):

        print(
            f"[DEBUG] RAW PATH: {path}"
        )

        # Resolve human-friendly paths
        resolved_path = self.path_resolver.resolve(path)

        print(
            f"[DEBUG] RESOLVED PATH: {resolved_path}"
        )

        # PathGuard expects a string
        validation = self.path_guard.validate(
            str(resolved_path)
        )

        print(
            f"[DEBUG] PATH VALIDATION: {validation}"
        )

        if not validation["allowed"]:
            return {
                "success": False,
                "error": validation["error"],
            }

        safe_path = Path(
            validation["path"]
        )

        print(
            f"[DEBUG] SAFE PATH: {safe_path}"
        )

        # -----------------------------------------
        # 2. Make sure it is a directory
        # -----------------------------------------

        if not safe_path.exists():

            return {
                "success": False,
                "error": (
                    f"Directory does not exist: "
                    f"{safe_path}"
                ),
            }

        if not safe_path.is_dir():

            return {
                "success": False,
                "error": (
                    f"Path is not a directory: "
                    f"{safe_path}"
                ),
            }

        # -----------------------------------------
        # 3. Read directory
        # -----------------------------------------

        try:

            entries = []

            for entry in safe_path.iterdir():

                try:

                    if entry.is_dir():

                        entries.append({
                            "name": entry.name,
                            "type": "directory",
                        })

                    else:

                        entries.append({
                            "name": entry.name,
                            "type": "file",
                            "size": entry.stat().st_size,
                        })

                except OSError:
                    # Ignore entries that cannot be inspected.
                    continue

            # Sort directories first, then files
            entries.sort(
                key=lambda item: (
                    item["type"] != "directory",
                    item["name"].lower(),
                )
            )

            return {
                "success": True,
                "path": str(safe_path),
                "count": len(entries),
                "entries": entries,
            }

        except PermissionError:

            return {
                "success": False,
                "error": (
                    f"Permission denied: "
                    f"{safe_path}"
                ),
            }

        except OSError as e:

            return {
                "success": False,
                "error": str(e),
            }