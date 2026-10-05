from __future__ import annotations

import os
import shutil
import sys
import threading
import tkinter as tk
from pathlib import Path
from typing import Callable, Optional

import customtkinter as ctk

from config import LOG_DIR, ZAPRET_LOG, TGPROXY_LOG, TOR_LOG, TOR_DIR, TOR_DEFAULT_PORT
from core import autostart, state_store, tg_link, fetch_vendor
from core.restart import restart_app
from core.tgproxy_settings import load_settings, save_settings, TgProxySettings
from gui import theme
from gui.splash import UpdateProgressDialog, _get_monitor_geometry

WINDOW_MIN_SIZE = (660, 540)

_GAME_FILTER_LABELS = {"off": "Выкл", "all": "Все", "tcp": "TCP", "udp": "UDP"}
_GAME_FILTER_MODES = {v: k for k, v in _GAME_FILTER_LABELS.items()}

_APPEARANCE_LABELS = {"dark": "Тёмная", "light": "Светлая", "system": "Как в системе"}
_APPEARANCE_MODES = {v: k for k, v in _APPEARANCE_LABELS.items()}


class AppWindow(ctk.CTk):
    def __init__(
        self,
        zapret,
        tgproxy,
        tor,
        state: state_store.AppState,
        on_exit: Callable[[], None],
        update_info: Optional[dict] = None,
        on_update_info: Optional[Callable[[dict], None]] = None,
    ):
        super().__init__()

        self.zapret = zapret
        self.tgproxy = tgproxy
        self.tor = tor
        self.app_state = state
        self._on_exit_cb = on_exit
        self._update_info: Optional[dict] = update_info
        self._on_update_info_cb = on_update_info
        self._destroyed = False
        self._tick_after_id: Optional[str] = None

        theme.setup_appearance(state.appearance_mode)
        self.configure(fg_color=theme.BG_DARK if state.appearance_mode != "light" else theme.BG_LIGHT)

        self.title("Fuck DPI")
        self.geometry(state.window_geometry or "700x580")
        self.minsize(*WINDOW_MIN_SIZE)
        if state.window_geometry is None:
            self._center_on_screen()
        try:
            self.iconbitmap(str(Path(__file__).resolve().parent.parent / "assets" / "icon.ico"))
        except Exception:
            pass

        self.protocol("WM_DELETE_WINDOW", self._on_close_button)

        self._build_layout()
        self._refresh_all()
        self._render_update_info()
        self._schedule_tick()
        self.withdraw()

    # ------------------------------------------------------------------
    # Потокобезопасный вызов из фоновых колбэков менеджеров процессов
    # ------------------------------------------------------------------

    def dispatch(self, fn: Callable[[], None]) -> None:
        try:
            self.after(0, fn)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Разметка
    # ------------------------------------------------------------------

    def _build_layout(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self._build_header()

        self.tabview = ctk.CTkTabview(
            self, corner_radius=14,
            fg_color=theme.card_color(), segmented_button_fg_color=theme.raised_color(),
            segmented_button_selected_color=theme.ACCENT, segmented_button_selected_hover_color=theme.ACCENT_HOVER,
        )
        self.tabview.grid(row=1, column=0, sticky="nsew", padx=theme.PAD, pady=(0, 8))

        self.tab_zapret = self.tabview.add("🛡  Zapret")
        self.tab_tgproxy = self.tabview.add("✈  TG Proxy")
        self.tab_tor = self.tabview.add("🧅  Tor")
        self.tab_settings = self.tabview.add("⚙  Настройки")
        self.tab_logs = self.tabview.add("🗒  Логи")

        try:
            self.tabview.set(self._tab_key_to_label(self.app_state.active_tab))
        except Exception:
            pass

        self._build_zapret_tab()
        self._build_tgproxy_tab()
        self._build_tor_tab()
        self._build_settings_tab()
        self._build_logs_tab()

        self.status_bar = ctk.CTkLabel(
            self, text="", anchor="w", text_color=theme.MUTED, font=theme.body_font(11)
        )
        self.status_bar.grid(row=2, column=0, sticky="ew", padx=22, pady=(0, 10))

    def _build_header(self) -> None:
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=theme.PAD, pady=(theme.PAD, 6))
        header.grid_columnconfigure(0, weight=1)

        title_box = ctk.CTkFrame(header, fg_color="transparent")
        title_box.grid(row=0, column=0, sticky="w")

        ctk.CTkLabel(title_box, text="Fuck DPI", font=theme.heading_font(20)).pack(anchor="w")
        ctk.CTkLabel(
            title_box, text="Обход блокировок и MTProto-прокси для Telegram",
            font=theme.body_font(12), text_color=theme.MUTED,
        ).pack(anchor="w", pady=(2, 0))

        badges = ctk.CTkFrame(header, fg_color="transparent")
        badges.grid(row=0, column=1, sticky="e")

        self.header_zapret_badge = theme.StatusBadge(badges, text="Zapret: остановлен", running=False)
        self.header_zapret_badge.pack(side="left", padx=(0, 8))
        self.header_tgproxy_badge = theme.StatusBadge(badges, text="TG Proxy: остановлен", running=False)
        self.header_tgproxy_badge.pack(side="left", padx=(0, 8))
        self.header_tor_badge = theme.StatusBadge(badges, text="Tor: остановлен", running=False)
        self.header_tor_badge.pack(side="left")

    _TAB_LABELS = {
        "Zapret": "🛡  Zapret",
        "TG Proxy": "✈  TG Proxy",
        "Tor": "🧅  Tor",
        "Автозапуск": "⚙  Настройки",
        "Настройки": "⚙  Настройки",
        "Логи": "🗒  Логи",
    }

    def _tab_key_to_label(self, key: str) -> str:
        return self._TAB_LABELS.get(key, "🛡  Zapret")

    def _tab_label_to_key(self, label: str) -> str:
        return {
            "🛡  Zapret": "Zapret",
            "✈  TG Proxy": "TG Proxy",
            "🧅  Tor": "Tor",
            "⚙  Настройки": "Настройки",
            "🗒  Логи": "Логи",
        }.get(label, "Zapret")

    # -- Zapret ----------------------------------------------------------

    def _build_zapret_tab(self) -> None:
        tab = self.tab_zapret
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(0, weight=1)
        scroll_frame = ctk.CTkScrollableFrame(tab, fg_color="transparent")
        scroll_frame.grid(row=0, column=0, sticky="nsew")
        scroll_frame.grid_columnconfigure(0, weight=1)

        card = theme.make_card(scroll_frame)
        card.grid(row=0, column=0, sticky="ew", padx=4, pady=(4, 12))
        card.grid_columnconfigure(1, weight=1)

        header = ctk.CTkFrame(card, fg_color="transparent")
        header.grid(row=0, column=0, columnspan=2, sticky="ew", padx=theme.PAD, pady=(theme.PAD, 6))
        header.grid_columnconfigure(0, weight=1)

        self.zapret_badge = theme.StatusBadge(header, text="Остановлен", running=False)
        self.zapret_badge.grid(row=0, column=0, sticky="w")

        self.zapret_toggle_btn = ctk.CTkButton(
            header, text="Запустить", width=130, height=34, corner_radius=10,
            fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER, command=self._toggle_zapret,
        )
        self.zapret_toggle_btn.grid(row=0, column=1, sticky="e")

        ctk.CTkFrame(card, fg_color=theme.border_color(), height=1).grid(
            row=1, column=0, columnspan=2, sticky="ew", padx=theme.PAD, pady=(4, 14)
        )

        ctk.CTkLabel(card, text="Стратегия обхода", font=theme.body_font(13), text_color=theme.MUTED).grid(
            row=2, column=0, columnspan=2, sticky="w", padx=theme.PAD
        )

        names = self.zapret.strategy_names or ["Нет стратегий -- проверьте vendor/zapret"]
        default_strategy = self.app_state.zapret_strategy if self.app_state.zapret_strategy in names else names[0]
        self.strategy_var = ctk.StringVar(value=default_strategy)
        self.strategy_menu = ctk.CTkOptionMenu(
            card, values=names, variable=self.strategy_var, height=34, corner_radius=10,
            fg_color=theme.raised_color(), button_color=theme.ACCENT, button_hover_color=theme.ACCENT_HOVER,
            command=self._on_strategy_selected,
        )
        self.strategy_menu.grid(row=3, column=0, columnspan=2, sticky="ew", padx=theme.PAD, pady=(6, 16))

        ctk.CTkLabel(card, text="Игровой фильтр (доп. порты вне стратегии)", font=theme.body_font(13),
                     text_color=theme.MUTED).grid(row=4, column=0, columnspan=2, sticky="w", padx=theme.PAD)

        self.game_filter_seg = ctk.CTkSegmentedButton(
            card, values=["Выкл", "Все", "TCP", "UDP"], command=self._on_game_filter_selected, height=34,
            fg_color=theme.raised_color(), selected_color=theme.ACCENT, selected_hover_color=theme.ACCENT_HOVER,
        )
        self.game_filter_seg.grid(row=5, column=0, columnspan=2, sticky="ew", padx=theme.PAD, pady=(6, theme.PAD))

        self._build_zapret_advanced_card(scroll_frame)

    def _build_zapret_advanced_card(self, tab) -> None:
        card = theme.make_card(tab)
        card.grid(row=1, column=0, sticky="ew", padx=4, pady=(0, 12))
        card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(card, text="Дополнительные настройки", font=theme.heading_font(15)).grid(
            row=0, column=0, sticky="w", padx=theme.PAD, pady=(theme.PAD, 2)
        )
        ctk.CTkLabel(
            card, text="Автоперезапуск и пользовательские домены -- без правки файлов вручную",
            font=theme.body_font(12), text_color=theme.MUTED,
        ).grid(row=1, column=0, sticky="w", padx=theme.PAD, pady=(0, 10))

        self.zapret_auto_restart_var = ctk.BooleanVar(value=self.app_state.zapret_auto_restart)
        ctk.CTkCheckBox(
            card, text="Автоматически перезапускать Zapret при неожиданном завершении",
            variable=self.zapret_auto_restart_var, font=theme.body_font(13),
            command=self._on_zapret_auto_restart_changed, checkbox_width=20, checkbox_height=20,
            fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER,
        ).grid(row=2, column=0, sticky="w", padx=theme.PAD, pady=(0, 14))

        ctk.CTkFrame(card, fg_color=theme.border_color(), height=1).grid(
            row=3, column=0, sticky="ew", padx=theme.PAD, pady=(0, 14)
        )

        ctk.CTkLabel(
            card, text="Свои домены под фильтр (list-general-user.txt) -- по одному на строку",
            font=theme.body_font(13), text_color=theme.MUTED,
        ).grid(row=4, column=0, sticky="w", padx=theme.PAD)
        self.general_user_box = ctk.CTkTextbox(
            card, height=70, font=theme.mono_font(12), fg_color=theme.raised_color(), corner_radius=10,
        )
        self.general_user_box.grid(row=5, column=0, sticky="ew", padx=theme.PAD, pady=(6, 14))
        self.general_user_box.insert("1.0", self.zapret.read_user_list("list-general-user.txt"))

        ctk.CTkLabel(
            card, text="Исключения из фильтрации, домены (list-exclude-user.txt)",
            font=theme.body_font(13), text_color=theme.MUTED,
        ).grid(row=6, column=0, sticky="w", padx=theme.PAD)
        self.exclude_user_box = ctk.CTkTextbox(
            card, height=70, font=theme.mono_font(12), fg_color=theme.raised_color(), corner_radius=10,
        )
        self.exclude_user_box.grid(row=7, column=0, sticky="ew", padx=theme.PAD, pady=(6, 14))
        self.exclude_user_box.insert("1.0", self.zapret.read_user_list("list-exclude-user.txt"))

        ctk.CTkLabel(
            card, text="Исключения из фильтрации, IP/подсети (ipset-exclude-user.txt)",
            font=theme.body_font(13), text_color=theme.MUTED,
        ).grid(row=8, column=0, sticky="w", padx=theme.PAD)
        self.ipset_exclude_box = ctk.CTkTextbox(
            card, height=70, font=theme.mono_font(12), fg_color=theme.raised_color(), corner_radius=10,
        )
        self.ipset_exclude_box.grid(row=9, column=0, sticky="ew", padx=theme.PAD, pady=(6, 6))
        self.ipset_exclude_box.insert("1.0", self.zapret.read_user_list("ipset-exclude-user.txt"))

        ctk.CTkButton(
            card, text="Сохранить списки", height=32, corner_radius=8, fg_color=theme.ACCENT,
            hover_color=theme.ACCENT_HOVER, command=self._save_user_lists,
        ).grid(row=10, column=0, sticky="w", padx=theme.PAD, pady=(4, theme.PAD))

    def _on_zapret_auto_restart_changed(self) -> None:
        enabled = bool(self.zapret_auto_restart_var.get())
        self.zapret.auto_restart_enabled = enabled
        self.app_state.zapret_auto_restart = enabled
        self._persist_state()

    def _save_user_lists(self) -> None:
        try:
            self.zapret.write_user_list("list-general-user.txt", self.general_user_box.get("1.0", "end"))
            self.zapret.write_user_list("list-exclude-user.txt", self.exclude_user_box.get("1.0", "end"))
            self.zapret.write_user_list("ipset-exclude-user.txt", self.ipset_exclude_box.get("1.0", "end"))
        except Exception as e:
            self._show_error(f"Не удалось сохранить списки\n{e}")
            return
        if self.zapret.is_running:
            self.zapret.restart_if_running()
            self._flash_status("Списки сохранены, Zapret перезапущен для применения")
        else:
            self._flash_status("Списки сохранены")
        self._refresh_zapret()

    def _on_strategy_selected(self, *_args) -> None:
        self.app_state.zapret_strategy = self.strategy_var.get()
        if self.zapret.is_running:
            self._start_zapret()
        self._persist_state()

    def _on_game_filter_selected(self, value: str) -> None:
        mode = _GAME_FILTER_MODES.get(value, "off")
        self.zapret.set_game_filter(mode)
        self.app_state.game_filter_mode = mode
        self._persist_state()
        self._refresh_zapret()

    def _toggle_zapret(self) -> None:
        if self.zapret.is_running:
            self.zapret.stop()
        else:
            self._start_zapret()
        self._refresh_zapret()
        self._persist_state()

    def _start_zapret(self) -> None:
        name = self.strategy_var.get()
        try:
            self.zapret.start(name)
            self.app_state.zapret_strategy = name
        except Exception as e:
            self._show_error(f"Zapret: не удалось запустить\n{e}")
        self._refresh_zapret()

    # -- TG Proxy ----------------------------------------------------------

    def _build_tgproxy_tab(self) -> None:
        tab = self.tab_tgproxy
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(0, weight=1)

        scroll_frame = ctk.CTkScrollableFrame(tab, fg_color="transparent")
        scroll_frame.grid(row=0, column=0, sticky="nsew")
        scroll_frame.grid_columnconfigure(0, weight=1)

        card = theme.make_card(scroll_frame)
        card.grid(row=0, column=0, sticky="ew", padx=4, pady=(4, 12))
        card.grid_columnconfigure(1, weight=1)

        header = ctk.CTkFrame(card, fg_color="transparent")
        header.grid(row=0, column=0, columnspan=2, sticky="ew", padx=theme.PAD, pady=(theme.PAD, 6))
        header.grid_columnconfigure(0, weight=1)

        self.tgproxy_badge = theme.StatusBadge(header, text="Остановлен", running=False)
        self.tgproxy_badge.grid(row=0, column=0, sticky="w")

        self.tgproxy_toggle_btn = ctk.CTkButton(
            header, text="Запустить", width=130, height=34, corner_radius=10,
            fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER, command=self._toggle_tgproxy,
        )
        self.tgproxy_toggle_btn.grid(row=0, column=1, sticky="e")

        ctk.CTkFrame(card, fg_color=theme.border_color(), height=1).grid(
            row=1, column=0, columnspan=2, sticky="ew", padx=theme.PAD, pady=(4, 14)
        )

        settings = load_settings()

        form = ctk.CTkFrame(card, fg_color="transparent")
        form.grid(row=2, column=0, columnspan=2, sticky="ew", padx=theme.PAD)
        form.grid_columnconfigure(1, weight=1)

        self._host_var = ctk.StringVar(value=settings.host)
        self._port_var = ctk.StringVar(value=str(settings.port))
        self._secret_var = ctk.StringVar(value=settings.secret)
        self._tls_var = ctk.StringVar(value=settings.fake_tls_domain or "")
        self._dc_var = ctk.StringVar(value=",".join(settings.dc_ips or []))

        rows = [
            ("Хост", self._host_var),
            ("Порт", self._port_var),
            ("Secret", self._secret_var),
            ("Fake TLS домен (необязательно)", self._tls_var),
            ("DC IP через запятую (необязательно)", self._dc_var),
        ]
        for i, (label, var) in enumerate(rows):
            ctk.CTkLabel(form, text=label, font=theme.body_font(12), text_color=theme.MUTED).grid(
                row=i, column=0, sticky="w", pady=6, padx=(0, 12)
            )
            ctk.CTkEntry(form, textvariable=var, height=32, corner_radius=8,
                         fg_color=theme.raised_color()).grid(row=i, column=1, sticky="ew", pady=6)

        btns = ctk.CTkFrame(card, fg_color="transparent")
        btns.grid(row=3, column=0, columnspan=2, sticky="ew", padx=theme.PAD, pady=(14, theme.PAD))

        ctk.CTkButton(btns, text="Сохранить настройки", height=32, corner_radius=8, fg_color=theme.ACCENT,
                      hover_color=theme.ACCENT_HOVER, command=self._save_tgproxy_settings).pack(
            side="left", padx=(0, 8)
        )
        ctk.CTkButton(btns, text="Открыть в Telegram", height=32, corner_radius=8, fg_color="transparent",
                      border_width=1, command=self._open_in_telegram).pack(side="left", padx=(0, 8))
        ctk.CTkButton(btns, text="Скопировать ссылку", height=32, corner_radius=8, fg_color="transparent",
                      border_width=1, command=self._copy_proxy_link).pack(side="left")

        self.tgproxy_auto_restart_var = ctk.BooleanVar(value=self.app_state.tgproxy_auto_restart)
        ctk.CTkCheckBox(
            card, text="Автоматически перезапускать TG WS Proxy при неожиданном завершении",
            variable=self.tgproxy_auto_restart_var, font=theme.body_font(13),
            command=self._on_tgproxy_auto_restart_changed, checkbox_width=20, checkbox_height=20,
            fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER,
        ).grid(row=4, column=0, columnspan=2, sticky="w", padx=theme.PAD, pady=(4, theme.PAD))

    def _on_tgproxy_auto_restart_changed(self) -> None:
        enabled = bool(self.tgproxy_auto_restart_var.get())
        self.tgproxy.auto_restart_enabled = enabled
        self.app_state.tgproxy_auto_restart = enabled
        self._persist_state()

    def _save_tgproxy_settings(self) -> None:
        try:
            port = int(self._port_var.get().strip())
        except ValueError:
            self._show_error("Порт должен быть числом")
            return
        dc_ips = [x.strip() for x in self._dc_var.get().split(",") if x.strip()]
        settings = TgProxySettings(
            host=self._host_var.get().strip() or "127.0.0.1",
            port=port,
            secret=self._secret_var.get().strip(),
            dc_ips=dc_ips or None,
            fake_tls_domain=self._tls_var.get().strip(),
        )
        save_settings(settings)
        self._persist_state()

        if not self.tgproxy.is_running:
            self._flash_status("Настройки TG WS Proxy сохранены")
            return

        # Перезапуск (stop+start) может занять несколько секунд из-за
        # корректного закрытия asyncio-сервера -- уводим в фоновый поток,
        # чтобы не подвешивать GUI.
        self.tgproxy_toggle_btn.configure(state="disabled")
        self._flash_status("Настройки сохранены, перезапускаем TG WS Proxy…")

        def worker() -> None:
            error = None
            try:
                self.tgproxy.stop()
                self.tgproxy.start()
            except Exception as e:
                error = str(e)
            self.dispatch(lambda: self._on_tgproxy_restart_done(error))

        threading.Thread(target=worker, daemon=True, name="tgproxy-restart").start()

    def _on_tgproxy_restart_done(self, error: Optional[str]) -> None:
        self.tgproxy_toggle_btn.configure(state="normal")
        if error:
            self._show_error(f"TG WS Proxy: не удалось перезапустить\n{error}")
        else:
            self._flash_status("TG WS Proxy перезапущен с новыми настройками")
        self._refresh_tgproxy()
        self._persist_state()

    def _toggle_tgproxy(self) -> None:
        # stop()/start() у TgProxyRuntime могут блокировать вызывающий поток на
        # несколько секунд (корректное закрытие asyncio-сервера, живые
        # соединения) -- выполняем в фоновом потоке, чтобы не подвешивать GUI.
        starting = not self.tgproxy.is_running
        self.tgproxy_toggle_btn.configure(state="disabled")
        self._flash_status("Запускаем TG WS Proxy…" if starting else "Останавливаем TG WS Proxy…")

        def worker() -> None:
            error = None
            try:
                if starting:
                    self.tgproxy.start()
                else:
                    self.tgproxy.stop()
            except Exception as e:
                error = str(e)
            self.dispatch(lambda: self._on_tgproxy_toggle_done(starting, error))

        threading.Thread(target=worker, daemon=True, name="tgproxy-toggle").start()

    def _on_tgproxy_toggle_done(self, starting: bool, error: Optional[str]) -> None:
        self.tgproxy_toggle_btn.configure(state="normal")
        if error:
            action = "запустить" if starting else "остановить"
            self._show_error(f"TG WS Proxy: не удалось {action}\n{error}")
        else:
            self._flash_status("TG WS Proxy запущен" if starting else "TG WS Proxy остановлен")
        self._refresh_tgproxy()
        self._persist_state()

    def _open_in_telegram(self) -> None:
        try:
            tg_link.open_in_telegram()
        except Exception as e:
            self._show_error(f"Не удалось открыть ссылку\n{e}")

    def _copy_proxy_link(self) -> None:
        try:
            link = tg_link.build_proxy_link()
            self.clipboard_clear()
            self.clipboard_append(link)
            self._flash_status("Ссылка скопирована в буфер обмена")
        except Exception as e:
            self._show_error(f"Не удалось собрать ссылку\n{e}")

    # -- Tor -------------------------------------------------------------

    def _build_tor_tab(self) -> None:
        tab = self.tab_tor
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(0, weight=1)

        scroll_frame = ctk.CTkScrollableFrame(tab, fg_color="transparent")
        scroll_frame.grid(row=0, column=0, sticky="nsew")
        scroll_frame.grid_columnconfigure(0, weight=1)

        card = theme.make_card(scroll_frame)
        card.grid(row=0, column=0, sticky="ew", padx=4, pady=(4, 12))
        card.grid_columnconfigure(1, weight=1)

        header = ctk.CTkFrame(card, fg_color="transparent")
        header.grid(row=0, column=0, columnspan=2, sticky="ew", padx=theme.PAD, pady=(theme.PAD, 6))
        header.grid_columnconfigure(0, weight=1)

        self.tor_badge = theme.StatusBadge(header, text="Остановлен", running=False)
        self.tor_badge.grid(row=0, column=0, sticky="w")

        self.tor_toggle_btn = ctk.CTkButton(
            header, text="Запустить", width=130, height=34, corner_radius=10,
            fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER, command=self._toggle_tor,
        )
        self.tor_toggle_btn.grid(row=0, column=1, sticky="e")

        ctk.CTkFrame(card, fg_color=theme.border_color(), height=1).grid(
            row=1, column=0, columnspan=2, sticky="ew", padx=theme.PAD, pady=(4, 14)
        )

        ctk.CTkLabel(
            card, text="SOCKS5-прокси запустится на 127.0.0.1:{порт}",
            font=theme.body_font(13), text_color=theme.MUTED,
        ).grid(row=2, column=0, columnspan=2, sticky="w", padx=theme.PAD)

        port_row = ctk.CTkFrame(card, fg_color="transparent")
        port_row.grid(row=3, column=0, columnspan=2, sticky="ew", padx=theme.PAD, pady=(6, 14))

        ctk.CTkLabel(port_row, text="Порт", font=theme.body_font(13), text_color=theme.MUTED).pack(
            side="left", padx=(0, 10)
        )
        self._tor_port_var = ctk.StringVar(value=str(self.app_state.tor_port or TOR_DEFAULT_PORT))
        ctk.CTkEntry(
            port_row, textvariable=self._tor_port_var, width=90, height=32, corner_radius=8,
            fg_color=theme.raised_color(),
        ).pack(side="left")

        btns = ctk.CTkFrame(card, fg_color="transparent")
        btns.grid(row=4, column=0, columnspan=2, sticky="ew", padx=theme.PAD, pady=(0, 14))

        ctk.CTkButton(
            btns, text="Сохранить настройки", height=32, corner_radius=8, fg_color=theme.ACCENT,
            hover_color=theme.ACCENT_HOVER, command=self._save_tor_settings,
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            btns, text="Скопировать адрес", height=32, corner_radius=8, fg_color="transparent",
            border_width=1, command=self._copy_tor_address,
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            btns, text="Очистить кеш", height=32, corner_radius=8, fg_color="transparent",
            border_width=1, command=self._clear_tor_cache,
        ).pack(side="left")

        self.tor_auto_restart_var = ctk.BooleanVar(value=self.app_state.tor_auto_restart)
        ctk.CTkCheckBox(
            card, text="Автоматически перезапускать Tor при неожиданном завершении",
            variable=self.tor_auto_restart_var, font=theme.body_font(13),
            command=self._on_tor_auto_restart_changed, checkbox_width=20, checkbox_height=20,
            fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER,
        ).grid(row=5, column=0, columnspan=2, sticky="w", padx=theme.PAD, pady=(4, theme.PAD))

        self._build_tor_bridges_card(scroll_frame)

    # ------- карточка «Мосты» ---------------------------------------------

    def _build_tor_bridges_card(self, parent) -> None:
        card = theme.make_card(parent)
        card.grid(row=1, column=0, sticky="ew", padx=4, pady=(0, 12))
        card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(card, text="Мосты (Bridges)", font=theme.heading_font(15)).grid(
            row=0, column=0, sticky="w", padx=theme.PAD, pady=(theme.PAD, 2)
        )
        ctk.CTkLabel(
            card,
            text="Помогают подключиться к Tor, когда провайдер блокирует прямые подключения",
            font=theme.body_font(12), text_color=theme.MUTED,
            wraplength=580, justify="left",
        ).grid(row=1, column=0, sticky="w", padx=theme.PAD, pady=(0, 10))

        self.tor_bridges_enabled_var = ctk.BooleanVar(value=self.app_state.tor_bridges_enabled)
        ctk.CTkCheckBox(
            card, text="Использовать мосты", variable=self.tor_bridges_enabled_var,
            font=theme.body_font(13), command=self._on_tor_bridges_enabled_changed,
            checkbox_width=20, checkbox_height=20,
            fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER,
        ).grid(row=2, column=0, sticky="w", padx=theme.PAD, pady=(0, 10))

        ctk.CTkLabel(
            card, text="Список мостов -- по одному на строку",
            font=theme.body_font(13), text_color=theme.MUTED,
        ).grid(row=3, column=0, sticky="w", padx=theme.PAD)

        self.tor_bridges_box = ctk.CTkTextbox(
            card, height=110, font=theme.mono_font(11),
            fg_color=theme.raised_color(), corner_radius=10,
        )
        self.tor_bridges_box.grid(row=4, column=0, sticky="ew", padx=theme.PAD, pady=(6, 10))
        self.tor_bridges_box.insert("1.0", self.app_state.tor_bridges or "")

        ctk.CTkLabel(
            card,
            text=(
                "Поддерживаются типы: obfs4, snowflake, meek_lite, obfs3.\n"
                "Пример строки obfs4: obfs4 192.0.2.1:443 0123456789ABCDEF... "
                "cert=abc... iat-mode=0\n\n"
                "Получить свои мосты можно на https://bridges.torproject.org/"
            ),
            font=theme.body_font(11), text_color=theme.MUTED,
            justify="left", wraplength=580,
        ).grid(row=5, column=0, sticky="w", padx=theme.PAD, pady=(0, 10))

        ctk.CTkButton(
            card, text="Сохранить мосты", height=32, corner_radius=8,
            fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER,
            command=self._save_tor_bridges,
        ).grid(row=6, column=0, sticky="w", padx=theme.PAD, pady=(0, theme.PAD))

    # ------- обработчики мостов -------------------------------------------

    def _on_tor_bridges_enabled_changed(self) -> None:
        self.app_state.tor_bridges_enabled = bool(self.tor_bridges_enabled_var.get())
        self._persist_state()

    def _get_tor_bridges(self) -> Optional[str]:
        """Возвращает текст мостов для TorManager.start() или None."""
        if not self.app_state.tor_bridges_enabled:
            return None
        text = (self.app_state.tor_bridges or "").strip()
        return text or None

    def _save_tor_bridges(self) -> None:
        text = self.tor_bridges_box.get("1.0", "end").rstrip()
        self.app_state.tor_bridges = text or None
        self.app_state.tor_bridges_enabled = bool(self.tor_bridges_enabled_var.get())
        self._persist_state()

        if not self.tor.is_running:
            self._flash_status("Настройки мостов Tor сохранены")
            return

        self.tor_toggle_btn.configure(state="disabled")
        self._flash_status("Мосты изменены, перезапускаем Tor…")

        def worker() -> None:
            error = None
            try:
                self.tor.stop()
                port = self.app_state.tor_port or TOR_DEFAULT_PORT
                self.tor.start(port, self._get_tor_bridges())
            except Exception as e:
                error = str(e)
            self.dispatch(lambda: self._on_tor_restart_done(error))

        threading.Thread(target=worker, daemon=True, name="tor-restart").start()

    # -- Tor: продолжение --------------------------------------------------

    def _on_tor_auto_restart_changed(self) -> None:
        enabled = bool(self.tor_auto_restart_var.get())
        self.tor.auto_restart_enabled = enabled
        self.app_state.tor_auto_restart = enabled
        self._persist_state()

    def _save_tor_settings(self) -> None:
        try:
            port = int(self._tor_port_var.get().strip())
            if not (1 <= port <= 65535):
                raise ValueError("Порт вне диапазона 1-65535")
        except ValueError as e:
            self._show_error(f"Некорректный порт: {e}")
            return
        self.app_state.tor_port = port
        self._persist_state()

        if not self.tor.is_running:
            self._flash_status("Настройки Tor сохранены")
            return

        self.tor_toggle_btn.configure(state="disabled")
        self._flash_status("Порт изменён, перезапускаем Tor…")

        def worker() -> None:
            error = None
            try:
                self.tor.stop()
                self.tor.start(port, self._get_tor_bridges())
            except Exception as e:
                error = str(e)
            self.dispatch(lambda: self._on_tor_restart_done(error))

        threading.Thread(target=worker, daemon=True, name="tor-restart").start()

    def _on_tor_restart_done(self, error: Optional[str]) -> None:
        self.tor_toggle_btn.configure(state="normal")
        if error:
            self._show_error(f"Tor: не удалось перезапустить\n{error}")
        else:
            self._flash_status("Tor перезапущен с новыми настройками")
        self._refresh_tor()
        self._persist_state()

    def _copy_tor_address(self) -> None:
        try:
            port = int(self._tor_port_var.get().strip())
        except ValueError:
            port = self.app_state.tor_port or TOR_DEFAULT_PORT
        address = f"127.0.0.1:{port}"
        self.clipboard_clear()
        self.clipboard_append(address)
        self._flash_status(f"Адрес SOCKS5 скопирован: {address}")

    def _clear_tor_cache(self) -> None:
        if self.tor.is_running:
            self._show_error("Сначала остановите Tor, чтобы очистить кеш")
            return
        data_dir = TOR_DIR / "Data"
        lock_file = TOR_DIR / "lock"
        try:
            if data_dir.exists():
                shutil.rmtree(data_dir)
            if lock_file.exists():
                lock_file.unlink()
            self._flash_status("Кеш Tor очищен")
        except Exception as e:
            self._show_error(f"Не удалось очистить кеш: {e}")

    def _toggle_tor(self) -> None:
        starting = not self.tor.is_running
        self.tor_toggle_btn.configure(state="disabled")
        self._flash_status("Запускаем Tor…" if starting else "Останавливаем Tor…")

        def worker() -> None:
            error = None
            try:
                if starting:
                    port = self.app_state.tor_port or TOR_DEFAULT_PORT
                    self.tor.start(port, self._get_tor_bridges())
                else:
                    self.tor.stop()
            except Exception as e:
                error = str(e)
            self.dispatch(lambda: self._on_tor_toggle_done(starting, error))

        threading.Thread(target=worker, daemon=True, name="tor-toggle").start()

    def _on_tor_toggle_done(self, starting: bool, error: Optional[str]) -> None:
        self.tor_toggle_btn.configure(state="normal")
        if error:
            action = "запустить" if starting else "остановить"
            self._show_error(f"Tor: не удалось {action}\n{error}")
        else:
            self._flash_status("Tor запущен" if starting else "Tor остановлен")
        self._refresh_tor()
        self._persist_state()

    # -- Настройки (автозапуск + поведение окна) --------------------------

    def _build_settings_tab(self) -> None:
        tab = self.tab_settings
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(0, weight=1)
        
        scroll_frame = ctk.CTkScrollableFrame(tab, fg_color="transparent")
        scroll_frame.grid(row=0, column=0, sticky="nsew")
        scroll_frame.grid_columnconfigure(0, weight=1)

        autostart_card = theme.make_card(scroll_frame)
        autostart_card.grid(row=0, column=0, sticky="ew", padx=4, pady=(4, 12))
        autostart_card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            autostart_card, text="Автозапуск", font=theme.heading_font(15),
        ).grid(row=0, column=0, sticky="w", padx=theme.PAD, pady=(theme.PAD, 2))
        ctk.CTkLabel(
            autostart_card, text="Один раз настроить -- дальше работает само",
            font=theme.body_font(12), text_color=theme.MUTED,
        ).grid(row=1, column=0, sticky="w", padx=theme.PAD, pady=(0, 10))

        self.admin_status_label = ctk.CTkLabel(
            autostart_card, text="", font=theme.body_font(13), text_color=theme.MUTED,
            justify="left", wraplength=560,
        )
        self.admin_status_label.grid(row=2, column=0, sticky="w", padx=theme.PAD, pady=(0, 14))

        self.autostart_btn = ctk.CTkButton(
            autostart_card, text="Включить автозапуск", height=34, corner_radius=10,
            fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER,
            command=self._toggle_autostart, width=220,
        )
        self.autostart_btn.grid(row=3, column=0, sticky="w", padx=theme.PAD)

        note = (
        "Как это работает: приложение создаёт задание в Планировщике заданий Windows\n"
        "с запуском \"при входе в систему\" и наивысшими правами. Такое задание не\n"
        "показывает окно UAC при каждом входе - запрос прав администратора нужен\n"
        "только сейчас, один раз, чтобы создать само задание.\n\n"
        "После включения приложение будет само запускаться при входе в Windows и\n"
        "восстанавливать последнюю запущенную стратегию Zapret и состояние TG WS\n"
        "Proxy - ничего перенастраивать не нужно."
        )
        ctk.CTkLabel(
            autostart_card, text=note, font=theme.body_font(12), text_color=theme.MUTED,
            justify="left", wraplength=580,
        ).grid(row=4, column=0, sticky="w", padx=theme.PAD, pady=(18, theme.PAD))

    
        window_card = theme.make_card(scroll_frame)
        window_card.grid(row=1, column=0, sticky="ew", padx=4, pady=(0, 12))
        window_card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(window_card, text="Окно и внешний вид", font=theme.heading_font(15)).grid(
            row=0, column=0, sticky="w", padx=theme.PAD, pady=(theme.PAD, 10)
        )

        self.minimize_to_tray_var = ctk.BooleanVar(value=self.app_state.minimize_to_tray_on_close)
        ctk.CTkCheckBox(
            window_card, text="Сворачивать в трей при закрытии окна (крестик)",
            variable=self.minimize_to_tray_var, font=theme.body_font(13),
            command=self._on_minimize_to_tray_changed, checkbox_width=20, checkbox_height=20,
            fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER,
        ).grid(row=1, column=0, sticky="w", padx=theme.PAD, pady=(0, 12))

        appearance_row = ctk.CTkFrame(window_card, fg_color="transparent")
        appearance_row.grid(row=2, column=0, sticky="w", padx=theme.PAD, pady=(0, theme.PAD))
        ctk.CTkLabel(appearance_row, text="Оформление", font=theme.body_font(13), text_color=theme.MUTED).pack(
            side="left", padx=(0, 10)
        )
        self.appearance_seg = ctk.CTkSegmentedButton(
            appearance_row, values=["Тёмная", "Светлая", "Как в системе"],
            command=self._on_appearance_selected,
            fg_color=theme.raised_color(), selected_color=theme.ACCENT,
            selected_hover_color=theme.ACCENT_HOVER,
        )
        self.appearance_seg.set(_APPEARANCE_LABELS.get(self.app_state.appearance_mode, "Тёмная"))
        self.appearance_seg.pack(side="left")

        self._build_updates_card(scroll_frame)

    def _build_updates_card(self, parent) -> None:
        card = theme.make_card(parent)
        card.grid(row=2, column=0, sticky="ew", padx=4, pady=(0, 12))
        card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(card, text="Обновления", font=theme.heading_font(15)).grid(
            row=0, column=0, sticky="w", padx=theme.PAD, pady=(theme.PAD, 2)
        )
        ctk.CTkLabel(
            card, text="При выходе новой версии Zapret и/или TG WS Proxy появится кнопка \"Обновить\"",
            font=theme.body_font(12), text_color=theme.MUTED,
        ).grid(row=1, column=0, sticky="w", padx=theme.PAD, pady=(0, 10))

        self.update_status_label = ctk.CTkLabel(
            card, text="Версии ещё не проверялись", font=theme.body_font(13), text_color=theme.MUTED,
            justify="left", wraplength=560,
        )
        self.update_status_label.grid(row=2, column=0, sticky="w", padx=theme.PAD, pady=(0, 12))

        btn_row = ctk.CTkFrame(card, fg_color="transparent")
        btn_row.grid(row=3, column=0, sticky="w", padx=theme.PAD, pady=(0, 6))

        self.check_updates_btn = ctk.CTkButton(
            btn_row, text="Проверить обновления", height=32, corner_radius=8, fg_color="transparent",
            border_width=1, command=self._check_updates,
        )
        self.check_updates_btn.pack(side="left", padx=(0, 8))

        self.do_update_btn = ctk.CTkButton(
            btn_row, text="Обновить", height=32, corner_radius=8, fg_color=theme.ACCENT,
            hover_color=theme.ACCENT_HOVER, command=self._do_update,
        )
        # Показывается только когда есть что обновлять (см. _render_update_info).

        self.check_updates_on_startup_var = ctk.BooleanVar(value=self.app_state.check_updates_on_startup)
        ctk.CTkCheckBox(
            card, text="Проверять обновления при запуске приложения",
            variable=self.check_updates_on_startup_var, font=theme.body_font(13),
            command=self._on_check_updates_on_startup_changed, checkbox_width=20, checkbox_height=20,
            fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER,
        ).grid(row=4, column=0, sticky="w", padx=theme.PAD, pady=(6, theme.PAD))

        self._render_update_info()

    def _on_check_updates_on_startup_changed(self) -> None:
        self.app_state.check_updates_on_startup = bool(self.check_updates_on_startup_var.get())
        self._persist_state()

    def _format_update_status(self, info: dict) -> str:
        if info.get("error"):
            return f"Не удалось проверить обновления: {info['error']}"
        zc = info.get("zapret_current") or "—"
        zl = info.get("zapret_latest") or "—"
        tc = (info.get("tgproxy_current") or "—")[:7]
        tl = (info.get("tgproxy_latest") or "—")[:7]
        lines = [f"Zapret: установлено {zc}, доступно {zl}", f"TG WS Proxy: установлен {tc}, доступен {tl}"]
        if info.get("update_available"):
            lines.append("Доступно обновление!")
        else:
            lines.append("У вас последняя версия.")
        return "\n".join(lines)

    def _render_update_info(self) -> None:
        if not hasattr(self, "update_status_label"):
            return
        info = self._update_info
        if info is None:
            self.update_status_label.configure(text="Версии ещё не проверялись")
            self.do_update_btn.pack_forget()
            return
        self.update_status_label.configure(text=self._format_update_status(info))
        if info.get("update_available"):
            self.do_update_btn.pack(side="left")
        else:
            self.do_update_btn.pack_forget()

    def _check_updates(self) -> None:
        if not hasattr(self, "check_updates_btn"):
            return
        self.check_updates_btn.configure(state="disabled", text="Проверка…")

        def worker() -> None:
            try:
                info = fetch_vendor.check_for_updates()
            except Exception as e:
                info = {"error": str(e), "update_available": False}
            self.dispatch(lambda: self._on_update_check_done(info))

        threading.Thread(target=worker, daemon=True, name="check-updates").start()

    def _on_update_check_done(self, info: dict) -> None:
        self._update_info = info
        self.check_updates_btn.configure(state="normal", text="Проверить обновления")
        self._render_update_info()
        if info.get("error"):
            self._flash_status("Не удалось проверить обновления")
        elif info.get("update_available"):
            self._flash_status("Доступно обновление!")
        else:
            self._flash_status("У вас последняя версия")

    def _do_update(self) -> None:
        from tkinter import messagebox
        if not messagebox.askyesno(
            "Обновление",
            "Zapret и TG WS Proxy будут остановлены, старые файлы удалены,\n"
            "загружены новые версии, после чего приложение перезапустится.\n\n"
            "Продолжить?",
        ):
            return

        # Блокируем кнопки на время всей операции.
        self.do_update_btn.configure(state="disabled")
        self.check_updates_btn.configure(state="disabled")
        self.zapret_toggle_btn.configure(state="disabled")
        self.tgproxy_toggle_btn.configure(state="disabled")
        self._flash_status("Обновление… не закрывайте приложение")

        # Окно прогресса -- то же, что при первом запуске, но поверх главного окна.
        dialog = UpdateProgressDialog(self)

        def worker() -> None:
            error = None
            try:
                # Останавливаем процессы в фоновом потоке: остановка tgproxy
                # может занять несколько секунд, а winws.exe нужно дождаться,
                # чтобы он отпустил файлы vendor/ перед удалением.
                self.zapret.stop()
                self.tgproxy.stop()
                self.dispatch(self._persist_state)
                fetch_vendor.perform_update(
                    progress_cb=lambda msg: dialog.dispatch(lambda m=msg: dialog.set_status(m))
                )
            except Exception as e:
                error = str(e)

            dialog.dispatch(dialog.close)
            if error is None:
                self.dispatch(restart_app)
            else:
                error_text = str(error)
                self.dispatch(lambda t=error_text: self._on_update_failed(t))

        threading.Thread(target=worker, daemon=True, name="perform-update").start()

    def _on_update_failed(self, message: str) -> None:
        self.do_update_btn.configure(state="normal")
        self.check_updates_btn.configure(state="normal")
        self.zapret_toggle_btn.configure(state="normal")
        self.tgproxy_toggle_btn.configure(state="normal")
        self._show_error(f"Не удалось обновить\n{message}")

    def apply_startup_update_info(self, info: dict) -> None:
        """Тихий результат фоновой проверки обновлений при запуске (без сплэша):
        обновляет вкладку «Настройки», трей и сообщает main.py через колбэк."""
        self._update_info = info
        self._render_update_info()
        if callable(self._on_update_info_cb):
            try:
                self._on_update_info_cb(info)
            except Exception:
                pass
        if info.get("update_available"):
            self._flash_status("Доступно обновление -- смотрите вкладку «Настройки»")

    def _toggle_autostart(self) -> None:
        if autostart.is_autostart_installed():
            ok, msg = autostart.remove_autostart()
        else:
            ok, msg = autostart.install_autostart()
        if ok:
            self._flash_status(msg)
        else:
            self._show_error(msg)
        self.app_state.autostart_installed = autostart.is_autostart_installed()
        self.app_state.setup_wizard_done = True
        self._persist_state()
        self._refresh_autostart()

    def _on_minimize_to_tray_changed(self) -> None:
        self.app_state.minimize_to_tray_on_close = bool(self.minimize_to_tray_var.get())
        self._persist_state()

    def _on_appearance_selected(self, value: str) -> None:
        mode = _APPEARANCE_MODES.get(value, "dark")
        self.app_state.appearance_mode = mode
        self._persist_state()
        self._flash_status("Оформление изменится после перезапуска приложения")

    # -- Логи ----------------------------------------------------------

    def _build_logs_tab(self) -> None:
        tab = self.tab_logs
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(0, weight=1)

        card = theme.make_card(tab)
        card.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)
        card.grid_columnconfigure(0, weight=1)
        card.grid_rowconfigure(2, weight=1)

        top = ctk.CTkFrame(card, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=theme.PAD, pady=(theme.PAD, 8))

        self.log_source_var = ctk.StringVar(value="zapret")
        ctk.CTkSegmentedButton(
            top, values=["zapret", "tgproxy", "tor"], variable=self.log_source_var,
            command=lambda *_a: self._refresh_logs(),
            fg_color=theme.raised_color(), selected_color=theme.ACCENT, selected_hover_color=theme.ACCENT_HOVER,
        ).pack(side="left")

        ctk.CTkButton(top, text="Обновить", width=90, height=30, corner_radius=8, fg_color="transparent",
                      border_width=1, command=self._refresh_logs).pack(side="left", padx=8)
        ctk.CTkButton(top, text="Открыть папку с логами", height=30, corner_radius=8, fg_color="transparent",
                      border_width=1, command=self._open_logs_folder).pack(side="left")

        ctk.CTkFrame(card, fg_color=theme.border_color(), height=1).grid(
            row=1, column=0, sticky="ew", padx=theme.PAD, pady=(0, 8)
        )

        self.log_box = ctk.CTkTextbox(card, font=theme.mono_font(11), fg_color=theme.raised_color(),
                                       corner_radius=10)
        self.log_box.grid(row=2, column=0, sticky="nsew", padx=theme.PAD, pady=(0, theme.PAD))
        self.log_box.configure(state="disabled")

    def _refresh_logs(self) -> None:
        source = self.log_source_var.get()
        if source == "zapret":
            path = ZAPRET_LOG
        elif source == "tgproxy":
            path = TGPROXY_LOG
        else:
            path = TOR_LOG
        try:
            text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else "(лог пока пуст)"
        except Exception as e:
            text = f"Не удалось прочитать лог: {e}"
        tail = "\n".join(text.splitlines()[-500:])
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.insert("1.0", tail)
        self.log_box.configure(state="disabled")
        self.log_box.see("end")

    def _open_logs_folder(self) -> None:
        try:
            if sys.platform.startswith("win"):
                os.startfile(LOG_DIR)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Обновление состояния виджетов
    # ------------------------------------------------------------------

    def _refresh_zapret(self) -> None:
        running = self.zapret.is_running
        text = f"Работает -- {self.zapret.current_strategy}" if running else "Остановлен"
        self.zapret_badge.set_state(text, running)
        self.header_zapret_badge.set_state(f"Zapret: {'работает' if running else 'остановлен'}", running)
        self.zapret_toggle_btn.configure(text="Остановить" if running else "Запустить")
        try:
            self.game_filter_seg.set(_GAME_FILTER_LABELS.get(self.app_state.game_filter_mode, "Выкл"))
        except Exception:
            pass

    def _refresh_tgproxy(self) -> None:
        running = self.tgproxy.is_running
        text = "Работает" if running else "Остановлен"
        self.tgproxy_badge.set_state(text, running)
        self.header_tgproxy_badge.set_state(f"TG Proxy: {'работает' if running else 'остановлен'}", running)
        self.tgproxy_toggle_btn.configure(text="Остановить" if running else "Запустить")

    def _refresh_tor(self) -> None:
        running = self.tor.is_running
        text = "Работает" if running else "Остановлен"
        self.tor_badge.set_state(text, running)
        self.header_tor_badge.set_state(f"Tor: {'работает' if running else 'остановлен'}", running)
        self.tor_toggle_btn.configure(text="Остановить" if running else "Запустить")

    def _refresh_autostart(self) -> None:
        installed = autostart.is_autostart_installed()
        admin = autostart.is_admin()
        lines = [
            "Права администратора: есть" if admin else "Права администратора: НЕТ (перезапустите от администратора)",
            "Автозапуск: включён" if installed else "Автозапуск: выключен",
        ]
        self.admin_status_label.configure(text="\n".join(lines))
        self.autostart_btn.configure(text="Выключить автозапуск" if installed else "Включить автозапуск")

    def _refresh_all(self) -> None:
        self._refresh_zapret()
        self._refresh_tgproxy()
        self._refresh_tor()
        self._refresh_autostart()
        self._refresh_logs()

    # ------------------------------------------------------------------
    # Сохранение состояния / жизненный цикл окна
    # ------------------------------------------------------------------

    def _persist_state(self) -> None:
        self.app_state.zapret_was_running = self.zapret.is_running
        self.app_state.tgproxy_was_running = self.tgproxy.is_running
        self.app_state.tor_was_running = self.tor.is_running
        try:
            self.app_state.active_tab = self._tab_label_to_key(self.tabview.get())
        except Exception:
            pass
        try:
            self.app_state.window_geometry = self.geometry()
        except Exception:
            pass
        state_store.save_state(self.app_state)

    def _schedule_tick(self) -> None:
        if self._destroyed:
            return
        try:
            self._tick_after_id = self.after(1500, self._tick)
        except tk.TclError:
            pass

    def _tick(self) -> None:
        if self._destroyed:
            return
        # Статус мог поменяться сам по себе (процесс упал) -- обновляем и
        # заодно подстраховочно пересохраняем состояние.
        self._refresh_zapret()
        self._refresh_tgproxy()
        self._refresh_tor()
        self._persist_state()
        self._schedule_tick()

    def destroy(self) -> None:
        self._destroyed = True
        if self._tick_after_id is not None:
            try:
                self.after_cancel(self._tick_after_id)
            except Exception:
                pass
        try:
            super().destroy()
        except tk.TclError:
            pass

    def _on_close_button(self) -> None:
        self.app_state.setup_wizard_done = True
        self._persist_state()
        if self.app_state.minimize_to_tray_on_close:
            self.withdraw()
        else:
            self.request_exit()

    def _center_on_screen(self) -> None:
        self.update_idletasks()
        width = max(self.winfo_width(), WINDOW_MIN_SIZE[0])
        height = max(self.winfo_height(), WINDOW_MIN_SIZE[1])
        mx, my, mw, mh = _get_monitor_geometry(self)
        x = mx + (mw - width) // 2
        y = my + (mh - height) // 2
        self.geometry(f"{width}x{height}+{x}+{y}")

    def show(self) -> None:
        self.deiconify()
        self.lift()
        self.focus_force()

    def show_settings_with_updates(self) -> None:
        self.show()
        try:
            self.tabview.set("⚙  Настройки")
        except Exception:
            pass
        self._check_updates()

    def request_exit(self) -> None:
        self._persist_state()
        self._on_exit_cb()

    # ------------------------------------------------------------------
    # Уведомления
    # ------------------------------------------------------------------

    def _flash_status(self, text: str) -> None:
        try:
            self.status_bar.configure(text=text)
        except tk.TclError:
            return
        except Exception:
            return
        try:
            self.after(4000, lambda: self.status_bar.configure(text=""))
        except tk.TclError:
            pass

    def _show_error(self, text: str) -> None:
        try:
            from tkinter import messagebox
            messagebox.showerror("Fuck DPI", text)
        except Exception:
            try:
                self.status_bar.configure(text=text)
            except tk.TclError:
                pass
