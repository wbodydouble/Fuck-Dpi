from __future__ import annotations

import threading
import time
from typing import Callable, List, Optional

from config import (
    WINWS_EXE,
    ZAPRET_DIR,
    ZAPRET_BIN_DIR,
    ZAPRET_LISTS_DIR,
    GAME_FILTER_FLAG_FILE,
    ZAPRET_LOG,
)
from core.process_manager import ManagedProcess
from core import bat_strategies

_AUTO_RESTART_DELAY_SEC = 2


class ZapretManager:
    def __init__(self, on_state_change: Optional[Callable[[str, bool], None]] = None):
        self._proc = ManagedProcess("zapret", ZAPRET_LOG, on_state_change)
        self._proc.on_crash = self._handle_crash
        self.current_strategy: Optional[str] = None

        self.auto_restart_enabled: bool = False

    # ---------------- Автоперезапуск при сбое ----------------

    def _handle_crash(self) -> None:
        if not self.auto_restart_enabled or not self.current_strategy:
            return
        strategy = self.current_strategy

        def _retry() -> None:
            time.sleep(_AUTO_RESTART_DELAY_SEC)
            try:
                self.start(strategy)
            except Exception:
                pass

        threading.Thread(target=_retry, daemon=True, name="zapret-auto-restart").start()

    # ---------------- Стратегии ----------------

    @property
    def strategy_names(self) -> List[str]:
        return [bat_strategies.strategy_display_name(p) for p in bat_strategies.list_strategy_files(ZAPRET_DIR)]

    def _find_bat(self, strategy_name: str):
        for p in bat_strategies.list_strategy_files(ZAPRET_DIR):
            if bat_strategies.strategy_display_name(p) == strategy_name:
                return p
        raise KeyError(
            f"Стратегия '{strategy_name}' не найдена среди файлов general*.bat в {ZAPRET_DIR}"
        )

    # ---------------- Игровой фильтр ----------------

    @property
    def game_filter_status(self) -> str:
        _, _, label = bat_strategies.read_game_filter(GAME_FILTER_FLAG_FILE)
        return label

    def set_game_filter(self, mode: str) -> None:
        bat_strategies.set_game_filter(GAME_FILTER_FLAG_FILE, mode)

        if self.is_running and self.current_strategy:
            self.start(self.current_strategy)

    # ---------------- Запуск/остановка ----------------

    @property
    def is_running(self) -> bool:
        return self._proc.is_running

    def start(self, strategy_name: str) -> None:
        bat_path = self._find_bat(strategy_name)

        bat_strategies.ensure_user_lists(ZAPRET_LISTS_DIR)
        game_tcp, game_udp, _ = bat_strategies.read_game_filter(GAME_FILTER_FLAG_FILE)

        args = bat_strategies.parse_bat_strategy(
            bat_path, ZAPRET_BIN_DIR, ZAPRET_LISTS_DIR, game_tcp, game_udp
        )

        self._proc.start(WINWS_EXE, args, cwd=ZAPRET_BIN_DIR)
        self.current_strategy = strategy_name

    def stop(self) -> None:
        self._proc.stop()
        self.current_strategy = None

    def restart_if_running(self) -> None:
        """Перезапускает текущую стратегию (используется после изменения
        пользовательских списков доменов, чтобы применить их без ручного
        нажатия кнопки)."""
        if self.is_running and self.current_strategy:
            self.start(self.current_strategy)

    # ---------------- Пользовательские списки доменов ----------------
    def read_user_list(self, filename: str) -> str:
        bat_strategies.ensure_user_lists(ZAPRET_LISTS_DIR)
        path = ZAPRET_LISTS_DIR / filename
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8", errors="replace")

    def write_user_list(self, filename: str, content: str) -> None:
        ZAPRET_LISTS_DIR.mkdir(parents=True, exist_ok=True)
        path = ZAPRET_LISTS_DIR / filename
        text = content.strip("\n")
        if not text.strip():
            text = "# пусто"
        path.write_text(text + "\n", encoding="utf-8")
