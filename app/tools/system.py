import os
import subprocess
import platform
import shutil
from pathlib import Path

from .base import Tool
from app.security.policy import SecurityPolicy
from app.tools.windows_apps import WindowsAppDiscovery
from app.tools.windows_launcher import WindowsLauncher





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

    name = "launch_application"

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

    def __init__(self):
        self.policy = SecurityPolicy()
        self.windows_apps = WindowsAppDiscovery()
        self.windows_launcher = WindowsLauncher()

    def execute(self, application: str):

        application = application.strip()

        print(
            f"[DEBUG] LaunchApplicationTool: {application}"
        )

        # -----------------------------------------
        # 1. Security check
        # -----------------------------------------

        if not self.policy.can_execute_application(
            application
        ):
            return {
                "success": False,
                "error": "Application execution denied.",
            }

        # -----------------------------------------
        # 2. Try normal executable / PATH
        # -----------------------------------------

        executable = self._find_executable(
            application
        )

        if executable:

            print(
                f"[DEBUG] Resolved executable: {executable}"
            )

            try:

                subprocess.Popen(
                    [executable],
                    shell=False,
                )

                return {
                    "success": True,
                    "application": application,
                    "executable": executable,
                }

            except Exception as e:

                return {
                    "success": False,
                    "application": application,
                    "error": str(e),
                }

        # -----------------------------------------
        # 3. Try Windows registered applications
        # -----------------------------------------

        app = self.windows_apps.find(
            application
        )

        if app:

            print(
                f"[DEBUG] Resolved Windows app: "
                f"{app['Name']} -> {app['AppID']}"
            )

            return self.windows_launcher.launch(
                app
            )

        # -----------------------------------------
        # 4. Nothing found
        # -----------------------------------------

        return {
            "success": False,
            "application": application,
            "error": (
                f"Could not find installed application "
                f"'{application}'."
            ),
        }

    def _find_executable(
        self,
        application: str,
    ):

        normalized = application.strip()

        candidates = [
            normalized,
            f"{normalized}.exe",
        ]

        for candidate in candidates:

            result = shutil.which(candidate)

            if result:
                return result

        return None