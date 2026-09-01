import re
from dataclasses import dataclass, field

# Words that mean "the thing we were just talking about". Context is only
# injected into the prompt when one of these appears -- otherwise every turn
# would pay tokens for state it never uses.
_REFERENTS = re.compile(
    r"\b(there|it|that|those|them|these|this|here|the folder|the file|"
    r"the files|same|again)\b",
    re.IGNORECASE,
)


@dataclass
class TaskState:
    """
    Short-term task memory, separate from conversation history.

    This is what makes 'create a folder called Projects' -> 'move the python
    files there' work. It is a handful of fields updated by tools, not a
    memory database.
    """

    last_directory: str | None = None
    last_created_directory: str | None = None
    last_application: str | None = None
    last_files: list[str] = field(default_factory=list)

    def record(self, tool_name: str, result: dict):
        if not isinstance(result, dict) or not result.get("success"):
            return

        if tool_name == "create_folder":
            self.last_created_directory = result.get("path")
            self.last_directory = result.get("path")

        elif tool_name in ("list_directory", "open_folder"):
            self.last_directory = result.get("path")

            entries = result.get("entries") or []
            self.last_files = [
                entry["name"]
                for entry in entries
                if isinstance(entry, dict) and entry.get("type") == "file"
            ]

        elif tool_name == "launch_application":
            self.last_application = result.get("application")

        elif tool_name in ("move_files", "delete_files"):
            self.last_directory = result.get("destination") or self.last_directory

    @staticmethod
    def mentions_referent(text: str) -> bool:
        return bool(_REFERENTS.search(text or ""))

    def as_context(self) -> str:
        """Compact context block, injected only when a referent is present."""
        parts = []

        if self.last_created_directory:
            parts.append(f"folder just created: {self.last_created_directory}")

        if self.last_directory:
            parts.append(f"current folder: {self.last_directory}")

        if self.last_application:
            parts.append(f"app just opened: {self.last_application}")

        if not parts:
            return ""

        return "CONTEXT (resolve words like 'there' and 'it' against this):\n" + "\n".join(
            f"- {part}" for part in parts
        )
