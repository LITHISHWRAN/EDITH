# app/security/policy.py
from dataclasses import dataclass
from pathlib import Path

from app.core.resolver.apps_index import BLOCKED_EXECUTABLES

RISK_SAFE = "safe"
RISK_SENSITIVE = "sensitive"
RISK_DESTRUCTIVE = "destructive"


@dataclass
class Decision:
    allowed: bool
    reason: str = ""
    requires_confirmation: bool = False


class SecurityPolicy:
    """
    Single authorization point. Tools perform operations; this decides
    whether the operation happens at all, and whether the user must approve.
    """

    def can_execute_application(self, application: str, target: str = "") -> Decision:
        if not isinstance(application, str) or not application.strip():
            return Decision(False, "Application name is empty.")

        # An application is identified by what it will actually run, not by
        # the label the model produced. Check the resolved target.
        executable = Path(target or application).name.lower()

        if not executable.endswith(".exe"):
            executable = f"{executable}.exe"

        if executable in BLOCKED_EXECUTABLES:
            return Decision(
                False,
                f"I'm not allowed to launch {executable}: "
                "shell and system-administration tools are off limits.",
            )

        return Decision(True)

    def authorize_tool(self, risk: str) -> Decision:
        if risk == RISK_DESTRUCTIVE:
            return Decision(True, requires_confirmation=True)

        return Decision(True)
