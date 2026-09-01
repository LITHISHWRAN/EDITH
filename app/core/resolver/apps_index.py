
import json
import os
import subprocess
import threading
import time
from pathlib import Path

from app.config import CACHE_DIR, CONFIG

CACHE_FILE = CACHE_DIR / "apps_index.json"

APP_PATHS_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"

# Executables EDITH must never launch on the user's behalf, regardless of
# how they are phrased. Enforced again in SecurityPolicy -- this is defence
# in depth, not the only gate.
BLOCKED_EXECUTABLES = {
    "pwsh.exe", "wscript.exe", "cscript.exe",
    "regedit.exe", "reg.exe", "mshta.exe", "rundll32.exe", "wmic.exe",
    "bcdedit.exe", "diskpart.exe", "vssadmin.exe", "certutil.exe",
    "schtasks.exe", "sc.exe", "net.exe", "net1.exe",
}


class AppsIndex:
    """
    Catalog of launchable applications on this machine.

    Built once, cached on disk, and warmed in the background at startup so
    the first 'open chrome' never pays for a PowerShell round-trip.
    """

    def __init__(self, ttl_seconds: int | None = None):
        self._entries: list[dict] | None = None
        self._ttl = ttl_seconds or CONFIG.apps_cache_ttl_seconds
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def warm_async(self):
        """Build the index off the critical path at startup."""
        threading.Thread(target=self.entries, daemon=True).start()

    def entries(self, force_refresh: bool = False) -> list[dict]:
        with self._lock:
            if self._entries is not None and not force_refresh:
                return self._entries

            entries = None if force_refresh else self._read_cache()

            if entries is None:
                entries = self._build()
                self._write_cache(entries)

            self._entries = entries
            return entries

    # ------------------------------------------------------------------
    # Building
    # ------------------------------------------------------------------

    def _build(self) -> list[dict]:
        entries: list[dict] = []
        entries.extend(self._from_app_paths())
        entries.extend(self._from_start_apps())
        return self._dedupe(entries)

    def _from_app_paths(self) -> list[dict]:
        """
        Registered executables. This is the source that resolves 'chrome',
        'brave', 'code' -- none of which are on PATH.
        """
        try:
            import winreg
        except ImportError:
            return []

        found: list[dict] = []

        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                root = winreg.OpenKey(hive, APP_PATHS_KEY)
            except OSError:
                continue

            with root:
                count = winreg.QueryInfoKey(root)[0]

                for index in range(count):
                    try:
                        subkey_name = winreg.EnumKey(root, index)

                        with winreg.OpenKey(root, subkey_name) as subkey:
                            raw_path, _ = winreg.QueryValueEx(subkey, "")

                    except OSError:
                        continue

                    if subkey_name.lower() in BLOCKED_EXECUTABLES:
                        continue

                    target = os.path.expandvars(str(raw_path).strip().strip('"'))

                    if not target or not Path(target).exists():
                        continue

                    # Registry keys are lowercase ('notepad.exe'), but this
                    # name gets spoken back to the user.
                    found.append({
                        "name": Path(subkey_name).stem.title(),
                        "kind": "exe",
                        "target": target,
                    })

        return found

    def _from_start_apps(self) -> list[dict]:
        """Start-menu entries, including UWP apps that have no .exe path."""
        script = "Get-StartApps | Select-Object Name, AppID | ConvertTo-Json -Compress"

        try:
            completed = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
                shell=False,
            )
        except (OSError, subprocess.SubprocessError):
            return []

        if completed.returncode != 0 or not completed.stdout.strip():
            return []

        try:
            data = json.loads(completed.stdout.strip())
        except json.JSONDecodeError:
            return []

        if isinstance(data, dict):
            data = [data]

        found = []

        for item in data:
            name = (item.get("Name") or "").strip()
            app_id = (item.get("AppID") or "").strip()

            if not name or not app_id:
                continue

            if Path(app_id).name.lower() in BLOCKED_EXECUTABLES:
                continue

            found.append({
                "name": name,
                "kind": "appid",
                "target": app_id,
            })

        return found

    @staticmethod
    def _dedupe(entries: list[dict]) -> list[dict]:
        # 'exe' entries are launched directly and can be verified by process
        # name, so they win over 'appid' entries for the same display name.
        best: dict[str, dict] = {}

        for entry in entries:
            key = " ".join(entry["name"].lower().split())
            existing = best.get(key)

            if existing is None or (
                existing["kind"] == "appid" and entry["kind"] == "exe"
            ):
                best[key] = entry

        return sorted(best.values(), key=lambda item: item["name"].lower())

    # ------------------------------------------------------------------
    # Cache
    # ------------------------------------------------------------------

    def _read_cache(self) -> list[dict] | None:
        try:
            payload = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

        if time.time() - payload.get("built_at", 0) > self._ttl:
            return None

        entries = payload.get("entries")

        return entries if isinstance(entries, list) and entries else None

    @staticmethod
    def _write_cache(entries: list[dict]):
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            CACHE_FILE.write_text(
                json.dumps({"built_at": time.time(), "entries": entries}),
                encoding="utf-8",
            )
        except OSError:
            pass
