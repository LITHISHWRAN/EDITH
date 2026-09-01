from app.tools.base import Tool

try:
    import pyperclip
except ImportError:
    pyperclip = None

MAX_PREVIEW = 120


def _unavailable() -> dict:
    return {
        "success": False,
        "error": "Clipboard access is unavailable on this system.",
    }


class ReadClipboardTool(Tool):

    name = "read_clipboard"

    description = (
        "Read the current text contents of the Windows clipboard. Use this "
        "when the user refers to what they have copied."
    )

    parameters = {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    }

    def execute(self, **kwargs):
        if pyperclip is None:
            return _unavailable()

        try:
            content = pyperclip.paste()

        except Exception as error:
            return {"success": False, "error": str(error)}

        if not content:
            return {
                "success": True,
                "empty": True,
                "content": "",
                "length": 0,
            }

        return {
            "success": True,
            "empty": False,
            "content": content,
            "length": len(content),
        }


class WriteClipboardTool(Tool):

    name = "write_clipboard"

    description = (
        "Copy text to the Windows clipboard so the user can paste it. Use "
        "this whenever the user asks for something to be copied, including "
        "generated code or corrected text. Pass the final text itself, "
        "never a description of it."
    )

    parameters = {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "The exact text to place on the clipboard.",
            }
        },
        "required": ["content"],
        "additionalProperties": False,
    }

    def execute(self, content: str):
        if pyperclip is None:
            return _unavailable()

        if content is None or content == "":
            return {
                "success": False,
                "error": "There was no content to copy.",
            }

        try:
            pyperclip.copy(content)
            # Read back: pyperclip fails silently on some Windows setups when
            # another process holds the clipboard, and reporting a copy that
            # did not happen is exactly the failure mode to avoid.
            written = pyperclip.paste()

        except Exception as error:
            return {"success": False, "error": str(error)}

        if written != content:
            return {
                "success": False,
                "error": (
                    "The clipboard did not accept the text -- another "
                    "application may be holding it."
                ),
            }

        preview = content if len(content) <= MAX_PREVIEW else content[:MAX_PREVIEW] + "..."

        return {
            "success": True,
            "length": len(content),
            "lines": content.count("\n") + 1,
            "preview": preview,
        }


class ClearClipboardTool(Tool):

    name = "clear_clipboard"

    description = "Clear the Windows clipboard."

    parameters = {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    }

    def execute(self, **kwargs):
        if pyperclip is None:
            return _unavailable()

        try:
            pyperclip.copy("")

        except Exception as error:
            return {"success": False, "error": str(error)}

        return {"success": True}
