
import time

try:
    import psutil
except ImportError:
    psutil = None

# Launching a UWP app goes through explorer.exe, which is always already
# running -- so it can never be the evidence that a launch worked.
IGNORED_PROCESSES = {
    "explorer.exe", "conhost.exe", "svchost.exe", "dllhost.exe",
    # Windows starts these *around* a packaged app launch. Treating one as
    # evidence produced 'Camera is open' on the strength of a broker
    # process, which says nothing about whether the app actually started.
    "runtimebroker.exe", "applicationframehost.exe", "backgroundtaskhost.exe",
    "wmiprvse.exe", "sihost.exe", "taskhostw.exe", "csrss.exe",
    "startmenuexperiencehost.exe", "searchhost.exe", "shellhost.exe",
    "audiodg.exe", "smartscreen.exe", "consent.exe", "wermgr.exe",
}


def snapshot_pids() -> set[int]:
    if psutil is None:
        return set()

    return set(psutil.pids())


def is_running(executable_name: str) -> bool:
    """
    Whether a process with this executable name already exists.

    Needed to avoid a false 'launch failed': Chrome and VS Code open a new
    window in the existing process, so no new PID ever appears.
    """
    if psutil is None or not executable_name:
        return False

    target = executable_name.lower()

    for process in psutil.process_iter(["name"]):
        try:
            name = process.info.get("name") or ""
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

        if name.lower() == target:
            return True

    return False


def wait_for_new_process(
    before: set[int],
    expected_name: str | None = None,
    timeout: float = 3.0,
    interval: float = 0.12,
    expected_is_a_guess: bool = False,
) -> dict:
    """
    Confirm a launch by observing a genuinely new process.

    Returns verified=None (unknown) rather than False when psutil is absent
    or the app reuses an existing process -- so the caller can phrase the
    reply honestly instead of asserting something it did not observe.
    """
    if psutil is None:
        return {"verified": None, "reason": "psutil-unavailable"}

    expected = expected_name.lower() if expected_name else None
    deadline = time.monotonic() + timeout
    saw_something = False

    while time.monotonic() < deadline:
        for pid in set(psutil.pids()) - before:
            try:
                name = psutil.Process(pid).name()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

            lowered = name.lower()

            if lowered in IGNORED_PROCESSES:
                continue

            if expected and lowered != expected:
                saw_something = True
                continue

            return {"verified": True, "process": name, "pid": pid}

        time.sleep(interval)

    # The name was derived from a package id, so failing to see it does not
    # mean the app failed to start -- only that we cannot prove it did.
    if expected_is_a_guess:
        return {
            "verified": None,
            "reason": "unrecognised-process" if saw_something else "no-new-process",
        }

    return {"verified": False, "reason": "no-new-process"}
