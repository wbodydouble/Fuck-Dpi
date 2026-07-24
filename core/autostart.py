from __future__ import annotations

import ctypes
import subprocess
import sys
from pathlib import Path
from typing import Tuple

TASK_NAME = "ZapretTgTray_Autostart"

_CREATIONFLAGS = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def is_windows() -> bool:
    return sys.platform.startswith("win")


def is_frozen() -> bool:
    """True, если это собранный PyInstaller-exe, а не запуск из исходников."""
    return bool(getattr(sys, "frozen", False))


def is_admin() -> bool:
    if not is_windows():
        return True
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin() -> bool:
    if not is_windows():
        return False

    exe = sys.executable
    if is_frozen():
        params = " ".join(f'"{a}"' for a in sys.argv[1:])
    else:
        script = str(Path(sys.argv[0]).resolve())
        params = " ".join(f'"{a}"' for a in [script, *sys.argv[1:]])

    # ShellExecuteW возвращает значение > 32 при успехе.
    result = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1)
    return int(result) > 32


def _exe_path_for_task() -> str:
    return str(Path(sys.executable).resolve())


def is_autostart_installed() -> bool:
    if not is_windows():
        return False
    res = subprocess.run(
        ["schtasks", "/Query", "/TN", TASK_NAME],
        capture_output=True,
        creationflags=_CREATIONFLAGS,
    )
    return res.returncode == 0


def install_autostart() -> Tuple[bool, str]:
    if not is_windows():
        return False, "Автозапуск поддерживается только на Windows"
    if not is_admin():
        return False, "Нужны права администратора (перезапустите приложение от администратора)"
    if not is_frozen():
        return False, "Автозапуск доступен только для собранного .exe (соберите через PyInstaller)"

    exe = _exe_path_for_task()
    result = subprocess.run(
        [
            "schtasks", "/Create",
            "/TN", TASK_NAME,
            "/TR", f'"{exe}"',
            "/SC", "ONLOGON",
            "/RL", "HIGHEST",
            "/F",
        ],
        capture_output=True,
        text=True,
        creationflags=_CREATIONFLAGS,
    )
    if result.returncode != 0:
        return False, (result.stderr or result.stdout or "Не удалось создать задание автозапуска").strip()
    return True, "Автозапуск включён: приложение будет запускаться при входе в Windows без запроса UAC"


def remove_autostart() -> Tuple[bool, str]:
    if not is_windows():
        return False, ""
    if not is_admin():
        return False, "Нужны права администратора"
    result = subprocess.run(
        ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"],
        capture_output=True,
        text=True,
        creationflags=_CREATIONFLAGS,
    )
    if result.returncode != 0:
        return False, (result.stderr or result.stdout or "Не удалось удалить задание автозапуска").strip()
    return True, "Автозапуск выключен"
