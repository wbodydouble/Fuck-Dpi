from __future__ import annotations

from pathlib import Path
from typing import Callable

import customtkinter as ctk

from config import ICON_ICO_PATH
from gui import theme

_WIDTH, _HEIGHT = 420, 200


class SplashWindow(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()

        theme.setup_appearance("dark")
        self.configure(fg_color=theme.BG_DARK)

        self.title("Fuck DPI")
        self.geometry(f"{_WIDTH}x{_HEIGHT}")
        self.resizable(False, False)
        self.overrideredirect(True)  # без рамки/заголовка -- чистый сплэш
        self._center_on_screen()

        try:
            self.iconbitmap(str(ICON_ICO_PATH))
        except Exception:
            pass

        self.attributes("-topmost", True)

        card = theme.make_card(self)
        card.pack(fill="both", expand=True, padx=1, pady=1)
        card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            card, text="Fuck DPI", font=theme.heading_font(17),
        ).grid(row=0, column=0, sticky="w", padx=24, pady=(24, 2))

        ctk.CTkLabel(
            card, text="Подготовка приложения к запуску…", font=theme.body_font(12),
            text_color=theme.MUTED,
        ).grid(row=1, column=0, sticky="w", padx=24, pady=(0, 16))

        self.progress = ctk.CTkProgressBar(
            card, mode="indeterminate", height=6, corner_radius=3,
            fg_color=theme.raised_color(), progress_color=theme.ACCENT,
        )
        self.progress.grid(row=2, column=0, sticky="ew", padx=24)
        self.progress.start()

        self.status_label = ctk.CTkLabel(
            card, text="Проверка файлов Zapret и TG WS Proxy…", font=theme.body_font(12),
            text_color=theme.MUTED, wraplength=_WIDTH - 60, justify="left", anchor="w",
        )
        self.status_label.grid(row=3, column=0, sticky="ew", padx=24, pady=(14, 24))

        self._closed = False

    def _center_on_screen(self) -> None:
        self.update_idletasks()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        x = (sw - _WIDTH) // 2
        y = (sh - _HEIGHT) // 2
        self.geometry(f"{_WIDTH}x{_HEIGHT}+{x}+{y}")

    # ------------------------------------------------------------------
    # Потокобезопасный доступ из фонового потока загрузки
    # ------------------------------------------------------------------

    def dispatch(self, fn: Callable[[], None]) -> None:
        if self._closed:
            return
        try:
            self.after(0, fn)
        except Exception:
            pass

    def set_status(self, text: str) -> None:
        if self._closed:
            return
        try:
            self.status_label.configure(text=text[:140])
        except Exception:
            pass

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.progress.stop()
        except Exception:
            pass
        try:
            self.destroy()
        except Exception:
            pass
