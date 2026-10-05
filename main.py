from __future__ import annotations

import threading
import time

import pystray
from PIL import Image

from config import ICON_PATH
from core import autostart, state_store, tg_link, fetch_vendor
from core.zapret_manager import ZapretManager
from core.tgproxy_manager import TgProxyManager
from core.tor_manager import TorManager
from gui.splash import SplashWindow

APP_NAME = "Fuck DPI"

_window = None  # type: ignore  # gui.app_window.AppWindow, создаётся в main()
_icon = None  # type: "pystray.Icon | None"
_update_info: dict | None = None  # результат последней проверки обновлений


# ---------------------------------------------------------------------------
# Реакция на изменение состояния процессов (вызывается из фоновых потоков)
# ---------------------------------------------------------------------------

def _on_state_change(name: str, running: bool) -> None:
    _refresh_tray()
    if _window is not None:
        _window.dispatch(_window._refresh_all)
        _window.dispatch(_window._persist_state)


zapret = ZapretManager(on_state_change=_on_state_change)
tgproxy = TgProxyManager(on_state_change=_on_state_change)
tor = TorManager(on_state_change=_on_state_change)


# ---------------------------------------------------------------------------
# Трей
# ---------------------------------------------------------------------------

def _refresh_tray() -> None:
    if _icon is not None:
        try:
            _icon.menu = _build_tray_menu()
            _icon.update_menu()
        except Exception:
            pass


def _show_window(icon=None, item=None) -> None:
    if _window is not None:
        _window.dispatch(_window.show)


def _show_updates(icon=None, item=None) -> None:
    if _window is not None:
        _window.dispatch(lambda: _window.show_settings_with_updates())


def _quick_toggle_zapret(icon=None, item=None) -> None:
    if zapret.is_running:
        zapret.stop()
    elif zapret.strategy_names:
        try:
            zapret.start(zapret.current_strategy or zapret.strategy_names[0])
        except Exception:
            pass
    _refresh_tray()


def _quick_toggle_tgproxy(icon=None, item=None) -> None:
    if tgproxy.is_running:
        tgproxy.stop()
    else:
        try:
            tgproxy.start()
        except Exception:
            pass
    _refresh_tray()


def _quick_toggle_tor(icon=None, item=None) -> None:
    if tor.is_running:
        tor.stop()
    else:
        try:
            state = state_store.load_state()
            bridges = state.tor_bridges if state.tor_bridges_enabled else None
            tor.start(bridges=bridges)
        except Exception:
            pass
    _refresh_tray()


def _exit_app(icon=None, item=None) -> None:
    zapret.stop()
    tgproxy.stop()
    tor.stop()
    if _window is not None:
        _window.app_state.zapret_was_running = False
        _window.app_state.tgproxy_was_running = False
        _window.app_state.tor_was_running = False
        state_store.save_state(_window.app_state)
        _window.dispatch(_window.destroy)
    if _icon is not None:
        _icon.stop()


def _build_tray_menu() -> pystray.Menu:
    zapret_running = zapret.is_running
    tgproxy_running = tgproxy.is_running
    tor_running = tor.is_running

    items = [
        pystray.MenuItem("Открыть окно", _show_window, default=True),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(
            f"Zapret: {'работает' if zapret_running else 'остановлен'}",
            _quick_toggle_zapret,
        ),
        pystray.MenuItem(
            f"TG WS Proxy: {'работает' if tgproxy_running else 'остановлен'}",
            _quick_toggle_tgproxy,
        ),
        pystray.MenuItem(
            f"Tor: {'работает' if tor_running else 'остановлен'}",
            _quick_toggle_tor,
        ),
        pystray.Menu.SEPARATOR
    ]

    if _update_info and _update_info.get("update_available"):
        items.append(pystray.Menu.SEPARATOR)
        items.append(pystray.MenuItem("⬆ Доступно обновление…", _show_updates))

    items.append(pystray.Menu.SEPARATOR)
    items.append(pystray.MenuItem("Выход", _exit_app))

    return pystray.Menu(*items)


def _run_tray() -> None:
    global _icon
    image = Image.open(ICON_PATH)
    _icon = pystray.Icon(APP_NAME, image, APP_NAME, _build_tray_menu())
    _icon.run()


# ---------------------------------------------------------------------------
# Восстановление последнего состояния
# ---------------------------------------------------------------------------

def _resume_last_session(state: state_store.AppState) -> None:
    """Поднимает то, что было запущено перед выключением/перезагрузкой."""
    if state.zapret_was_running and state.zapret_strategy in zapret.strategy_names:
        try:
            zapret.start(state.zapret_strategy)
        except Exception:
            pass
    if state.tgproxy_was_running:
        try:
            tgproxy.start()
        except Exception:
            pass
    if state.tor_was_running:
        try:
            bridges = state.tor_bridges if state.tor_bridges_enabled else None
            tor.start(state.tor_port, bridges)
        except Exception:
            pass
    _refresh_tray()


# ---------------------------------------------------------------------------
# Тихая проверка обновлений (без сплэша)
# ---------------------------------------------------------------------------

def _set_update_info(info: dict) -> None:
    global _update_info
    _update_info = info
    _refresh_tray()


def _quiet_update_check(state: state_store.AppState) -> None:
    """Фоновая проверка обновлений при обычном запуске: никакого окна,
    результат тихо появляется в трее и на вкладке «Настройки», когда готов."""
    if not state.check_updates_on_startup:
        return
    try:
        info = fetch_vendor.check_for_updates()
    except Exception:
        return
    if _window is not None:
        _window.dispatch(lambda: _window.apply_startup_update_info(info))


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------

def main() -> None:
    global _window

    state = state_store.load_state()
    zapret.auto_restart_enabled = state.zapret_auto_restart
    tgproxy.auto_restart_enabled = state.tgproxy_auto_restart
    tor.auto_restart_enabled = state.tor_auto_restart

    # Права администратора нужны драйверу WinDivert -- запрашиваем UAC до
    # каких-либо окон, чтобы не показывать сплэш зря.
    if autostart.is_windows() and not autostart.is_admin():
        if autostart.relaunch_as_admin():
            return

    # Сплэш показываем ТОЛЬКО когда vendor-файлов нет (первый запуск или
    # повреждённая установка) и их действительно нужно скачивать с GitHub.
    # Во всех остальных случаях приложение стартует молча, сразу в трей.
    if not fetch_vendor.vendor_ready():
        splash = SplashWindow()

        def _download_worker() -> None:
            try:
                fetch_vendor.ensure_vendor(
                    progress_cb=lambda msg: splash.dispatch(lambda m=msg: splash.set_status(m))
                )
            except Exception as e:
                error_text = str(e)
                # Даём пользователю время прочитать ошибку до закрытия сплэша.
                splash.dispatch(lambda t=error_text: splash.set_status(f"Ошибка при загрузке файлов: {t}"))
                time.sleep(4)
            splash.dispatch(splash.close)

        threading.Thread(target=_download_worker, daemon=True, name="startup").start()
        splash.mainloop()

    threading.Thread(target=_run_tray, daemon=True).start()

    from gui.app_window import AppWindow

    _window = AppWindow(
        zapret, tgproxy, tor, state,
        on_exit=_exit_app,
        update_info=_update_info,
        on_update_info=_set_update_info,
    )
    try:
        _window.after(600, lambda: _resume_last_session(state))
    except Exception:
        pass

    if not state.setup_wizard_done:
        try:
            _window.after(300, _window.show)
        except Exception:
            pass

    # Обычный запуск: проверяем обновления тихо, в фоне, без окна загрузки.
    threading.Thread(target=_quiet_update_check, args=(state,), daemon=True, name="update-check").start()

    _window.mainloop()


if __name__ == "__main__":
    main()
