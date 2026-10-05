from __future__ import annotations

import os
import subprocess
import sys


def restart_app() -> None:
    exe = sys.executable
    if getattr(sys, "frozen", False):
        args = [exe, *sys.argv[1:]]
    else:
        args = [exe, str(sys.argv[0]), *sys.argv[1:]]

    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    subprocess.Popen(args, close_fds=True, creationflags=creationflags)
    os._exit(0)
