import subprocess


class WindowsLauncher:

    def launch(self, app):

        if not app:
            return {
                "success": False,
                "error": "Application was not found.",
            }

        app_id = app.get("AppID")

        if not app_id:

            return {
                "success": False,
                "error": "Application has no AppID.",
            }

        try:

            subprocess.Popen(
                [
                    "explorer.exe",
                    f"shell:AppsFolder\\{app_id}",
                ],
                shell=False,
            )

            return {
                "success": True,
                "name": app["Name"],
                "app_id": app_id,
            }

        except Exception as e:

            return {
                "success": False,
                "name": app["Name"],
                "app_id": app_id,
                "error": str(e),
            }