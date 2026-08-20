import subprocess
import json


class WindowsAppDiscovery:

    def find(self, application: str):

        application = application.strip()

        if not application:
            return None

        apps = self._get_apps()

        if not apps:
            return None

        normalized = self._normalize(application)

        # Exact match
        for app in apps:

            if self._normalize(app["Name"]) == normalized:
                return app

        # Contains match
        for app in apps:

            name = self._normalize(app["Name"])

            if normalized in name:
                return app

        return None

    def _get_apps(self):

        ps_script = """
Get-StartApps |
    Select-Object Name, AppID |
    ConvertTo-Json -Compress
"""

        try:

            result = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    ps_script,
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                shell=False,
            )

        except Exception:
            return []

        if result.returncode != 0:
            return []

        output = result.stdout.strip()

        if not output:
            return []

        try:

            data = json.loads(output)

        except json.JSONDecodeError:

            return []

        if isinstance(data, dict):
            return [data]

        return data

    @staticmethod
    def _normalize(value: str):

        return (
            value
            .strip()
            .lower()
            .replace("-", " ")
            .replace("_", " ")
        )