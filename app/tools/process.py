"""
Closing applications.

Closing is not the reverse of launching: a launch either happens or does
not, whereas a close can lose unsaved work. So the default is a polite
WM_CLOSE -- the same thing clicking the X does, letting the app show its own
"save changes?" prompt -- and forcing is a separate, confirmed step.
"""

import ctypes
import os
import re
import time
from ctypes import wintypes
from pathlib import Path

from app.core.resolver.apps_index import AppsIndex
from app.core.resolver.catalog import APP_ALIASES, normalize, similarity
from app.tools.base import Tool

try:
    import psutil
except ImportError:
    psutil = None

# Closing these breaks the desktop or the machine. explorer.exe is the shell
# itself: killing it takes the taskbar and every File Explorer window with it.
PROTECTED_PROCESSES = {
    "system", "registry", "idle",
    "smss.exe", "csrss.exe", "wininit.exe", "winlogon.exe", "services.exe",
    "lsass.exe", "svchost.exe", "dwm.exe", "explorer.exe", "fontdrvhost.exe",
    "sihost.exe", "ctfmon.exe", "taskhostw.exe", "runtimebroker.exe",
    "searchhost.exe", "startmenuexperiencehost.exe", "shellexperiencehost.exe",
    "audiodg.exe", "conhost.exe", "dllhost.exe", "spoolsv.exe",
}

WM_CLOSE = 0x0010

GRACEFUL_TIMEOUT = 6.0
FORCE_TIMEOUT = 4.0

# How long to let a window appear before deciding the process has none.
# Measured: Camera's frame shows up ~0.6s after launch, but a cold start of
# a packaged app is slower, and concluding too early escalates to a kill.
WINDOW_WAIT = 5.0


def _match_score(query: str, process_stem: str) -> float:
    """
    Score a spoken name against an executable name.

    Matches in both directions, unlike the launcher's scorer: a process name
    is usually shorter than what the user says, so 'file explorer' has to
    match explorer.exe and 'google chrome' has to match chrome.exe.
    """
    query_norm = normalize(query)
    process_norm = normalize(process_stem)

    query_words = set(query_norm.split())
    process_words = set(process_norm.split())

    if not query_words or not process_words:
        return 0.0

    if process_words <= query_words:
        return 1.0

    # Packaged apps glue the publisher onto the name: the Camera app runs as
    # WindowsCamera.exe, which scores only 0.63 against 'camera' by edit
    # distance. Containment catches those, and 'close cam' with it.
    compact_query = query_norm.replace(" ", "")
    compact_process = process_norm.replace(" ", "")

    if len(compact_query) >= 3 and compact_query in compact_process:
        return 0.90

    return similarity(query, process_stem)


def _display_name(process_name: str, apps: AppsIndex) -> str:
    """
    A name worth saying back. 'WindowsCamera.exe' should not be read out as
    'Windowscamera'.
    """
    stem = Path(process_name).stem

    # Prefer the name Windows shows in the Start menu.
    for entry in apps.entries():
        if entry["kind"] != "exe":
            continue

        if Path(entry["target"]).name.lower() == process_name.lower():
            return entry["name"]

    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", stem).strip()

    return spaced if spaced.isupper() or " " in spaced else stem.title()


def _own_process_tree() -> set[int]:
    """EDITH's own pid and its ancestors -- never a valid thing to close."""
    pids = {os.getpid()}

    if psutil is None:
        return pids

    try:
        process = psutil.Process()

        for parent in process.parents():
            pids.add(parent.pid)

    except Exception:
        pass

    return pids


def _window_class(user32, hwnd) -> str:
    buffer = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buffer, 256)
    return buffer.value


def _owner_pid(user32, hwnd) -> int:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def _closable_windows(pids: set[int]) -> list:
    """
    Top-level windows that belong to these processes.

    Packaged (UWP) apps do not own their own window: ApplicationFrameHost
    hosts the frame, and the app's real window is a CoreWindow child inside
    it. Searching only by owning pid finds nothing for Camera, Calculator,
    Photos and every other Store app -- so those frames are matched through
    their child instead.
    """
    if not hasattr(ctypes, "WinDLL"):
        return []

    user32 = ctypes.WinDLL("user32", use_last_error=True)

    top_level = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    child_level = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    found = []

    def hosts_our_core_window(frame) -> bool:
        belongs = False

        def visit_child(child, _lparam):
            nonlocal belongs

            if _window_class(user32, child) == "Windows.UI.Core.CoreWindow":
                if _owner_pid(user32, child) in pids:
                    belongs = True
                    return False

            return True

        try:
            user32.EnumChildWindows(frame, child_level(visit_child), 0)
        except OSError:
            return False

        return belongs

    def visit(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True

        if _owner_pid(user32, hwnd) in pids:
            found.append(hwnd)
            return True

        if _window_class(user32, hwnd) == "ApplicationFrameWindow":
            if hosts_our_core_window(hwnd):
                found.append(hwnd)

        return True

    try:
        user32.EnumWindows(top_level(visit), 0)

    except OSError:
        return found

    return found


def _post_close_to_windows(pids: set[int]) -> int:
    """Ask every window belonging to these processes to close."""
    if not hasattr(ctypes, "WinDLL"):
        return 0

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    windows = _closable_windows(pids)

    for hwnd in windows:
        user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)

    return len(windows)


def _visible_window_count(pids: set[int]) -> int:
    """How many windows these processes still show, UWP frames included."""
    return len(_closable_windows(pids))


def _still_running(pids: set[int]) -> set[int]:
    if psutil is None:
        return set()

    alive = set()

    for pid in pids:
        try:
            process = psutil.Process(pid)

            if process.is_running() and process.status() != psutil.STATUS_ZOMBIE:
                alive.add(pid)

        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    return alive


def _wait_for_exit(pids: set[int], timeout: float) -> set[int]:
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        remaining = _still_running(pids)

        if not remaining:
            return set()

        time.sleep(0.15)

    return _still_running(pids)


class CloseApplicationTool(Tool):

    name = "close_application"

    risk = "sensitive"

    description = (
        "Close a running application by name, the same way clicking its X "
        "button would. The application can still prompt to save unsaved "
        "work. Use this when the user asks to close, quit or exit an app."
    )

    parameters = {
        "type": "object",
        "properties": {
            "application": {
                "type": "string",
                "description": "Name of the application to close.",
            }
        },
        "required": ["application"],
        "additionalProperties": False,
    }

    def __init__(self, apps_index: AppsIndex | None = None):
        self.apps = apps_index or AppsIndex()

    def execute(self, application: str, _confirmed: bool = False):
        application = (application or "").strip()

        if not application:
            return {"success": False, "error": "Tell me which app to close."}

        if psutil is None:
            return {
                "success": False,
                "error": "I can't manage processes: psutil is not installed.",
            }

        matches, label, blocked = self._find(application)

        if blocked:
            # 'not running' would be a false explanation: it is running, and
            # I am declining to close it.
            return {
                "success": False,
                "application": blocked,
                "protected": True,
                "error": (
                    "I won't close myself."
                    if blocked == "EDITH"
                    else (
                        "I run as a Python process myself, so I won't close "
                        "Python -- I can't tell which one is me."
                        if blocked == "SELF_KIND"
                        else f"I won't close {blocked} -- Windows needs it running."
                    )
                ),
            }

        if not matches:
            return {
                "success": False,
                "application": application,
                "not_running": True,
                "error": f"{application} does not appear to be running.",
            }

        pids = {process.pid for process in matches}

        # Forcing is only reachable through a confirmed follow-up.
        if _confirmed:
            return self._force(pids, label)

        # An app launched a moment ago may not have drawn its window yet, and
        # a slow starter like Slack can take seconds. Concluding "no window"
        # too early would escalate to killing a process that was about to be
        # perfectly closable.
        posted = 0
        deadline = time.monotonic() + WINDOW_WAIT

        while True:
            posted = _post_close_to_windows(pids)

            if posted or time.monotonic() >= deadline:
                break

            time.sleep(0.2)

            # Re-resolve every pass. Several Windows 11 apps, Notepad among
            # them, start a stub that exits and hands off to the real
            # process under a new pid -- so a pid set captured once goes
            # stale and the window is never found.
            matches, found_label, _ = self._find(application)

            if matches:
                pids = {process.pid for process in matches}
                label = found_label
                continue

            # No match this pass. During a stub handoff there is a moment
            # with no process at all, so keep the previous pids and retry
            # rather than concluding there is no window.
            if not _still_running(pids):
                return {
                    "success": True,
                    "application": label,
                    "closed": len(pids),
                    "verified": True,
                    "forced": False,
                    "already_gone": True,
                }

        if posted == 0:
            # A background process with no window cannot be asked politely.
            return {
                "success": False,
                "requires_confirmation": True,
                "summary": (
                    f"{label} has no window to close, so I would have to "
                    f"end the process. Any unsaved work would be lost."
                ),
                "preview": {"count": len(pids), "names": [label]},
            }

        remaining = _wait_for_exit(pids, GRACEFUL_TIMEOUT)

        if not remaining:
            return {
                "success": True,
                "application": label,
                "closed": len(pids),
                "verified": True,
                "forced": False,
            }

        # Slack, Discord and Teams hide in the notification area instead of
        # exiting. The window really did close, so reporting failure would
        # contradict what the user just watched happen.
        if _visible_window_count(remaining) == 0:
            return {
                "success": True,
                "application": label,
                "closed": len(pids),
                "verified": True,
                "forced": False,
                "to_tray": True,
            }

        # A window is still up: almost always a "save changes?" dialog
        # waiting on the user. Report that instead of killing it.
        return {
            "success": False,
            "requires_confirmation": True,
            "summary": (
                f"{label} did not close -- it may be asking you to save "
                f"something. I can force it to quit, but unsaved work "
                f"would be lost."
            ),
            "preview": {"count": len(remaining), "names": [label]},
        }

    # ------------------------------------------------------------------

    @staticmethod
    def _force(pids: set[int], label: str) -> dict:
        for pid in pids:
            try:
                psutil.Process(pid).terminate()

            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        remaining = _wait_for_exit(pids, FORCE_TIMEOUT)

        for pid in remaining:
            try:
                psutil.Process(pid).kill()

            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        remaining = _wait_for_exit(pids, 2.0)

        if remaining:
            return {
                "success": False,
                "application": label,
                "error": (
                    f"I could not close {label}. It may need administrator "
                    f"rights."
                ),
            }

        return {
            "success": True,
            "application": label,
            "closed": len(pids),
            "verified": True,
            "forced": True,
        }

    def _find(self, application: str):
        """Returns (processes, display label, blocked_label_or_None)."""
        raw_query = normalize(application)
        query = APP_ALIASES.get(raw_query, raw_query)

        # Match on both spellings. Aliases are written for launching -- for
        # closing, 'explorer' has to still match explorer.exe even though
        # the alias rewrites it to 'file explorer'.
        queries = {raw_query, query}

        expected = self._expected_executable(query)
        protect = _own_process_tree()

        try:
            own_executable = psutil.Process().name().lower()
        except Exception:
            own_executable = "python.exe"

        scored = []
        blocked = None

        for process in psutil.process_iter(["name", "pid"]):
            try:
                name = process.info.get("name") or ""
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

            lowered = name.lower()

            if not lowered:
                continue

            stem = Path(name).stem
            best_score = max(_match_score(q, stem) for q in queries)
            hit = (expected and lowered == expected) or best_score >= 0.86

            # EDITH runs as python.exe and cannot tell its own interpreter
            # from any other. Closing "python" would be a coin flip between
            # a stray script, the user's work, and EDITH itself.
            if hit and lowered == own_executable:
                blocked = blocked or "SELF_KIND"
                continue

            # System processes are checked first. EDITH is usually started
            # from a terminal launched by Explorer, so explorer.exe is in
            # our own ancestry -- reporting 'I won't close myself' would be
            # the wrong reason for the right refusal.
            if lowered in PROTECTED_PROCESSES:
                if hit:
                    blocked = blocked or Path(name).stem.title()

                continue

            if process.pid in protect:
                if hit:
                    blocked = blocked or "EDITH"

                continue

            if expected and lowered == expected:
                scored.append((1.0, process, name))
                continue

            if best_score >= 0.86:
                scored.append((best_score, process, name))

        if not scored:
            return [], application, blocked

        best = max(score for score, _, _ in scored)

        # Close every window of the one app, not a mix of look-alikes.
        winner = next(name for score, _, name in scored if score == best)

        matches = [
            process
            for _, process, name in scored
            if name.lower() == winner.lower()
        ]

        return matches, _display_name(winner, self.apps), None

    def _expected_executable(self, query: str) -> str | None:
        best, best_score = None, 0.0

        for entry in self.apps.entries():
            if entry["kind"] != "exe":
                continue

            score = similarity(query, entry["name"])

            if score > best_score:
                best, best_score = entry, score

        if best is None or best_score < 0.72:
            return None

        return Path(best["target"]).name.lower()
