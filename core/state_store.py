from __future__ import annotations

import json
import threading
from dataclasses import dataclass, asdict, fields
from pathlib import Path
from typing import Optional

from config import DATA_DIR

STATE_FILE = DATA_DIR / "app_state.json"

_lock = threading.Lock()


@dataclass
class AppState:
    # ---------- Zapret ----------
    zapret_was_running: bool = False
    zapret_strategy: Optional[str] = None
    game_filter_mode: str = "off"  # off | all | tcp | udp

    # ---------- TG WS Proxy ----------
    tgproxy_was_running: bool = False

    # ---------- Первый запуск / автозапуск ----------
    setup_wizard_done: bool = False
    autostart_installed: bool = False

    # ---------- Окно ----------
    window_geometry: Optional[str] = None  # "WxH+X+Y"
    active_tab: str = "Zapret"
    appearance_mode: str = "dark"  # dark | light | system
    minimize_to_tray_on_close: bool = True

    # ---------- Доп. настройки (устойчивость к сбоям) ----------
    zapret_auto_restart: bool = False
    tgproxy_auto_restart: bool = False

    # ---------- Обновления ----------
    check_updates_on_startup: bool = True


def load_state() -> AppState:
    if not STATE_FILE.exists():
        return AppState()
    try:
        raw = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        # Битый/неполный файл (например, после аварийного завершения) --
        # не роняем приложение, просто стартуем с состоянием по умолчанию.
        return AppState()

    valid_keys = {f.name for f in fields(AppState)}
    filtered = {k: v for k, v in raw.items() if k in valid_keys}
    try:
        return AppState(**filtered)
    except Exception:
        return AppState()


def save_state(state: AppState) -> None:
    with _lock:
        try:
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp = STATE_FILE.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(asdict(state), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            tmp.replace(STATE_FILE)
        except Exception:
            pass
