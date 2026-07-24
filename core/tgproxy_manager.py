from __future__ import annotations

import threading
import time
from typing import Callable, Optional

from config import TGPROXY_LOG
from core.tgproxy_runtime import TgProxyRuntime
from core.tgproxy_settings import load_settings

_AUTO_RESTART_DELAY_SEC = 2


class TgProxyManager:
    def __init__(self, on_state_change: Optional[Callable[[str, bool], None]] = None):
        self._runtime = TgProxyRuntime("tgproxy", TGPROXY_LOG, on_state_change)
        self._runtime.on_crash = self._handle_crash
        self.auto_restart_enabled: bool = False

    @property
    def is_running(self) -> bool:
        return self._runtime.is_running

    def start(self) -> None:
        settings = load_settings()
        self._runtime.start(settings)

    def stop(self) -> None:
        self._runtime.stop()

    def _handle_crash(self) -> None:
        if not self.auto_restart_enabled:
            return

        def _retry() -> None:
            time.sleep(_AUTO_RESTART_DELAY_SEC)
            try:
                self.start()
            except Exception:
                pass

        threading.Thread(target=_retry, daemon=True, name="tgproxy-auto-restart").start()
