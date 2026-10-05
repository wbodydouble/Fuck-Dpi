from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable, Optional

from config import TOR_EXE, TOR_DIR, TOR_LOG
from core.process_manager import ManagedProcess

_AUTO_RESTART_DELAY_SEC = 2


# транспорт в строке моста -> (транспорты для tor.exe, возможные имена exe-плагина)
_BRIDGE_TRANSPORT_PLUGINS = {
    "obfs2":        (["obfs2", "obfs3", "obfs4", "scramblesuit"], ["lyrebird.exe", "obfs4proxy.exe"]),
    "obfs3":        (["obfs2", "obfs3", "obfs4", "scramblesuit"], ["lyrebird.exe", "obfs4proxy.exe"]),
    "obfs4":        (["obfs2", "obfs3", "obfs4", "scramblesuit"], ["lyrebird.exe", "obfs4proxy.exe"]),
    "scramblesuit": (["obfs2", "obfs3", "obfs4", "scramblesuit"], ["lyrebird.exe", "obfs4proxy.exe"]),
    "snowflake":    (["snowflake"], ["snowflake-client.exe"]),
    "meek_lite":    (["meek_lite"], ["meek-client.exe"]),
    "meek":         (["meek_lite"], ["meek-client.exe"]),
}


def _find_pt_exe(candidates: list[str]) -> Optional[Path]:
    """Ищет исполняемый файл pluggable transport'а в vendor/tor."""
    for base in (TOR_DIR / "pluggable_transports", TOR_DIR):
        for name in candidates:
            p = base / name
            if p.exists():
                return p
    return None


def _build_bridge_args(bridges_text: Optional[str]) -> list[str]:
    """Преобразует текст со списком мостов в аргументы для tor.exe."""
    if not bridges_text:
        return []

    lines = [
        ln.strip() for ln in bridges_text.splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    if not lines:
        return []

    args: list[str] = []
    needed: list[tuple[list[str], list[str]]] = []
    seen_keys: set[tuple[str, ...]] = set()

    for raw in lines:
        line = raw
        # Некоторые пользователи копируют строки с префиксом "Bridge " -- Tor
        # его не ожидает внутри значения --Bridge.
        if line.lower().startswith("bridge "):
            line = line[len("bridge "):].strip()
        if not line:
            continue

        args.extend(["--Bridge", line])

        head = line.split(None, 1)[0].lower() if line.split() else ""
        mapping = _BRIDGE_TRANSPORT_PLUGINS.get(head)
        if mapping:
            key = tuple(mapping[1])
            if key not in seen_keys:
                seen_keys.add(key)
                needed.append(mapping)

    for transports, exe_names in needed:
        exe = _find_pt_exe(exe_names)
        if exe is None:
            continue
        exe_str = str(exe)
        # Tor на Windows сам оборачивает путь в кавычки при запуске плагина.
        # Если мы добавим свои, получится ""C:\...\lyrebird.exe"" -- Windows
        # не найдёт такой файл, PID 0, плагин не стартует, obfs4 отваливается.
        # Кавычки нужны только если в пути есть пробелы.
        if " " in exe_str:
            exe_str = f'"{exe_str}"'
        args.extend([
            "--ClientTransportPlugin",
            f'{",".join(transports)} exec {exe_str}',
        ])

    return args


class TorManager:
    def __init__(self, on_state_change: Optional[Callable[[str, bool], None]] = None):
        self._proc = ManagedProcess("tor", TOR_LOG, on_state_change)
        self._proc.on_crash = self._handle_crash
        self.auto_restart_enabled: bool = False
        self._current_port: int = 9050
        self._current_bridges: Optional[str] = None

    @property
    def is_running(self) -> bool:
        return self._proc.is_running

    @property
    def current_port(self) -> int:
        return self._current_port

    def start(self, port: int = 9050, bridges: Optional[str] = None) -> None:
        self._current_port = port
        self._current_bridges = bridges
        data_dir = TOR_DIR / "Data"
        data_dir.mkdir(parents=True, exist_ok=True)
        args = [
            f"--SocksPort", f"127.0.0.1:{port}",
            f"--DataDirectory", str(data_dir),
        ]
        args.extend(_build_bridge_args(bridges))
        self._proc.start(TOR_EXE, args, cwd=TOR_DIR)

    def stop(self) -> None:
        self._proc.stop()

    def _handle_crash(self) -> None:
        if not self.auto_restart_enabled:
            return

        def _retry() -> None:
            time.sleep(_AUTO_RESTART_DELAY_SEC)
            try:
                self.start(self._current_port, self._current_bridges)
            except Exception:
                pass

        threading.Thread(target=_retry, daemon=True, name="tor-auto-restart").start()
