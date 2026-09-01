"""
Launching other programs without letting them talk to EDITH's terminal.

By default Popen hands the parent's stdin/stdout/stderr to the child, so a
chatty app such as Slack or VS Code writes its own debug output into the
conversation. Every launch in EDITH goes through spawn() instead.
"""

import subprocess
import sys

if sys.platform == "win32":
    # DETACHED_PROCESS: the child does not attach to our console.
    # CREATE_NEW_PROCESS_GROUP: Ctrl-C in EDITH does not kill it.
    _CREATION_FLAGS = (
        subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    )
else:
    _CREATION_FLAGS = 0


def spawn(args, cwd: str | None = None) -> subprocess.Popen:
    """
    Start a program detached, with its output discarded.

    Raises OSError like Popen does; callers report the failure rather than
    assuming the launch worked.
    """
    return subprocess.Popen(
        args,
        cwd=cwd,
        shell=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=_CREATION_FLAGS,
        start_new_session=sys.platform != "win32",
    )
