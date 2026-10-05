from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable, Optional, Sequence


if sys.platform == "win32":
    _CREATIONFLAGS = subprocess.CREATE_NO_WINDOW
else:
    _CREATIONFLAGS = 0


class ManagedProcess:
    """Запускает один процесс, следит за его состоянием, умеет останавливать."""

    def __init__(
        self,
        name: str,
        log_file: Path,
        on_state_change: Optional[Callable[[str, bool], None]] = None,
    ):
        self.name = name
        self.log_file = log_file
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._on_state_change = on_state_change  # callback(name, is_running)
        self._manual_stop = False
        self.on_crash: Optional[Callable[[], None]] = None

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._proc is not None and self._proc.poll() is None

    def start(self, exe_path: Path, args: Sequence[str] = (), cwd: Optional[Path] = None) -> None:
        with self._lock:
            self._stop_locked()
            self._manual_stop = False

            if not exe_path.exists():
                raise FileNotFoundError(
                    f"[{self.name}] Не найден исполняемый файл: {exe_path}\n"
                    f"Проверьте config.py и разложенные файлы в vendor/."
                )

            log_handle = open(self.log_file, "ab", buffering=0)

            self._proc = subprocess.Popen(
                [str(exe_path), *args],
                cwd=str(cwd) if cwd else str(exe_path.parent),
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                creationflags=_CREATIONFLAGS,
            )

        self._notify(True)

        # Следим за неожиданным завершением процесса в фоновом потоке.
        watcher = threading.Thread(target=self._watch, daemon=True)
        watcher.start()

    def _watch(self) -> None:
        proc = self._proc
        if proc is None:
            return
        proc.wait()
        was_manual = self._manual_stop
        with self._lock:
            if self._proc is proc:
                self._proc = None
        self._notify(False)
        if not was_manual and self.on_crash:
            try:
                self.on_crash()
            except Exception:
                pass

    def stop(self) -> None:
        with self._lock:
            self._manual_stop = True
            self._stop_locked()
        self._notify(False)

    def _stop_locked(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            try:
                self._proc.terminate()
                try:
                    # Дожидаемся фактического завершения: пока процесс жив,
                    # Windows держит его файлы открытыми (winws.exe, DLL
                    # WinDivert), и их нельзя удалить/заменить при обновлении.
                    self._proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
                    try:
                        self._proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        pass
            except Exception:
                pass
        self._proc = None

    def _notify(self, running: bool) -> None:
        if self._on_state_change:
            try:
                self._on_state_change(self.name, running)
            except Exception:
                pass
