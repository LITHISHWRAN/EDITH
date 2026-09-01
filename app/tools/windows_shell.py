from .base import Tool
from app.tools.launch import spawn


class OpenWindowsShellTool(Tool):

    name = "open_windows_shell"

    description = (
        "Open special Windows Shell locations that are not normal "
        "files or executable applications, such as Recycle Bin."
    )

    parameters = {
        "type": "object",
        "properties": {
            "location": {
                "type": "string",
                "enum": [
                    "recycle_bin",
                ],
                "description": "Special Windows Shell location to open.",
            }
        },
        "required": ["location"],
        "additionalProperties": False,
    }

    SHELL_TARGETS = {
        "recycle_bin": "shell:RecycleBinFolder",
    }

    def execute(self, location: str):

        target = self.SHELL_TARGETS.get(location)

        if not target:
            return {
                "success": False,
                "error": f"Unsupported Windows Shell location: {location}",
            }

        try:

            spawn(["explorer.exe", target])

            return {
                "success": True,
                "location": location,
            }

        except Exception as e:

            return {
                "success": False,
                "error": str(e),
            }