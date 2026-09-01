import os
import subprocess
import platform
import shutil
from datetime import datetime
from pathlib import Path

from .base import Tool
from app.core.resolver.apps_index import AppsIndex
from app.core.resolver.catalog import APP_ALIASES, normalize, similarity
from app.security.policy import SecurityPolicy
from app.tools.launch import spawn
from app.tools.verification import (
    is_running,
    snapshot_pids,
    wait_for_new_process,
)


def _expected_from_app_id(app_id: str) -> str | None:
    """
    Guess the executable name of a packaged app from its AppID.

    'Microsoft.WindowsCamera_8wekyb3d8bbwe!App' -> 'windowscamera.exe'.
    Without this there is nothing to verify against, and any unrelated
    process starting nearby gets counted as proof the app launched.
    """
    family = (app_id or "").split("!", 1)[0]
    package = family.split("_", 1)[0]

    if "." not in package:
        return None

    name = package.rsplit(".", 1)[-1].strip()

    return f"{name.lower()}.exe" if name else None


class GetCurrentTimeTool(Tool):

    name = "get_current_time"

    description = (
        "Get the current local time from the computer."
    )

    parameters = {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    }

    def execute(self, **kwargs):
        current_time = datetime.now()
        formatted_time = current_time.strftime("%I:%M %p").lstrip("0")

        return {
            "success": True,
            "time": formatted_time,
            "iso_time": current_time.isoformat(timespec="seconds"),
        }

class SystemInfoTool(Tool):

    name = "get_system_info"

    description = (
        "Get basic information about the local computer "
        "such as operating system, machine architecture, "
        "and Python version."
    )
    
    parameters = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    def execute(self, **kwargs):
        return {
            "operating_system": platform.system(),
            "os_version": platform.version(),
            "machine": platform.machine(),
            "python_version": platform.python_version(),
        }
        




class LaunchApplicationTool(Tool):
    
    SPECIAL_LOCATIONS = {
        "recycle bin": "shell:RecycleBinFolder",
        "recyclebin": "shell:RecycleBinFolder",
        "bin": "shell:RecycleBinFolder",
        "trash": "shell:RecycleBinFolder",
    }

    name = "launch_application"

    risk = "sensitive"

    description = (
        "Launch an installed Windows application. "
        "The application may be specified using its normal "
        "display name, such as Chrome, WhatsApp, VS Code, "
        "Notepad, Paint, Calculator, Camera, Settings, "
        "or Windows Security."
    )

    parameters = {
        "type": "object",
        "properties": {
            "application": {
                "type": "string",
                "description": (
                    "Name of the Windows application to launch."
                ),
            }
        },
        "required": ["application"],
        "additionalProperties": False,
    }

    def __init__(self, apps_index: AppsIndex | None = None):
        self.policy = SecurityPolicy()
        # Shared with the router so the PowerShell scan happens once per
        # session, not once per launch.
        self.apps = apps_index or AppsIndex()

    def execute(self, application: str):

        application = (application or "").strip()

        if not application:
            return {
                "success": False,
                "error": "No application was specified.",
            }

        special_location = self.SPECIAL_LOCATIONS.get(
            normalize(application)
        )

        if special_location:
            return self._open_shell_location(special_location)

        # -----------------------------------------
        # 1. Resolve the name to something real
        # -----------------------------------------

        entry = self._resolve(application)

        if entry is None:
            return {
                "success": False,
                "application": application,
                "error": (
                    f"I could not find an installed application "
                    f"called '{application}'."
                ),
            }

        # -----------------------------------------
        # 2. Authorize the resolved target
        # -----------------------------------------

        decision = self.policy.can_execute_application(
            application,
            entry["target"],
        )

        if not decision.allowed:
            return {
                "success": False,
                "application": entry["name"],
                "error": decision.reason,
            }

        # -----------------------------------------
        # 3. Launch, then look for evidence
        # -----------------------------------------

        expected = (
            Path(entry["target"]).name
            if entry["kind"] == "exe"
            else _expected_from_app_id(entry["target"])
        )

        # Chrome and VS Code open a new window inside the process that is
        # already running, so no new PID would ever appear. Record that up
        # front instead of reporting a false failure.
        already_running = is_running(expected) if expected else False

        before = snapshot_pids()

        try:
            if entry["kind"] == "exe":
                spawn(
                    [entry["target"]],
                    cwd=str(Path(entry["target"]).parent),
                )

            else:
                spawn([
                    "explorer.exe",
                    "shell:AppsFolder\\" + entry["target"],
                ])

        except OSError as error:
            return {
                "success": False,
                "application": entry["name"],
                "error": str(error),
            }

        if already_running:
            verification = {
                "verified": True,
                "reason": "already-running",
            }

        else:
            verification = wait_for_new_process(
                before,
                expected_name=expected,
                expected_is_a_guess=entry["kind"] != "exe",
            )

        # success reports that the launch was issued without error.
        # verified reports whether we actually observed it come up.
        # They are different claims and the caller must not conflate them.
        return {
            "success": True,
            "application": entry["name"],
            "target": entry["target"],
            **verification,
        }

    # ----------------------------------------------------------

    def _resolve(self, application: str) -> dict | None:
        query = normalize(application)
        query = APP_ALIASES.get(query, query)

        best = None
        best_score = 0.0

        for entry in self.apps.entries():
            score = similarity(query, entry["name"])

            if score > best_score:
                best, best_score = entry, score

        if best is None or best_score < 0.72:
            return None

        return best

    @staticmethod
    def _open_shell_location(target: str) -> dict:
        try:
            spawn(["explorer.exe", target])

            return {
                "success": True,
                "application": "Recycle Bin",
                "verified": None,
            }

        except OSError as error:
            return {
                "success": False,
                "application": "Recycle Bin",
                "error": str(error),
            }
