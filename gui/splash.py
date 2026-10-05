from __future__ import annotations

import sys
import tkinter as tk
from typing import Callable, Tuple

import customtkinter as ctk

from config import ICON_ICO_PATH
from gui import theme

_WIDTH, _HEIGHT = 420, 200


def _get_monitor_geometry(widget: ctk.CTk) -> Tuple[int, int, int, int]:
    """Возвращает (x, y, width, height) монитора, на котором находится курсор.

    Fallback: размеры виртуального десктопа и позицию (0, 0).
    """
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            class RECT(ctypes.Structure):
                _fields_ = [
                    ("left", wintypes.LONG),
                    ("top", wintypes.LONG),
                    ("right", wintypes.LONG),
                    ("bottom", wintypes.LONG),
                ]

            class MONITORINFO(ctypes.Structure):
                _fields_ = [
                    ("cbSize", wintypes.DWORD),
                    ("rcMonitor", RECT),
                    ("rcWork", RECT),
                    ("dwFlags", wintypes.DWORD),
                ]

            user32 = ctypes.windll.user32
            pt = wintypes.POINT()
            if user32.GetCursorPos(ctypes.byref(pt)):
                hmon = user32.MonitorFromPoint(pt, 2)  # MONITOR_DEFAULTTONEAREST
                if hmon:
                    mi = MONITORINFO()
                    mi.cbSize = ctypes.sizeof(mi)
                    if user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
                        rect = mi.rcWork
                        return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top
    except Exception:
        pass

    # Fallback
    return 0, 0, widget.winfo_screenwidth(), widget.winfo_screenheight()


class _ProgressPopupBase:
    """Общая начинка окон прогресса: карточка, заголовок, индикатор, статус.

    Подкласс сам создаёт окно (SplashWindow -- CTk, UpdateProgressDialog --
    CTkToplevel) и вызывает _build_ui(). Здесь же потокобезопасные set_status /
    dispatch / close для вызова из фоновых потоков загрузки."""

    def _build_ui(self, title: str, subtitle: str, status_text: str) -> None:
        self.configure(fg_color=theme.BG_DARK)

        try:
            self.iconbitmap(str(ICON_ICO_PATH))
        except Exception:
            pass

        try:
            self.attributes("-topmost", True)
        except Exception:
            pass

        card = theme.make_card(self)
        card.pack(fill="both", expand=True, padx=1, pady=1)
        card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            card, text=title, font=theme.heading_font(17),
        ).grid(row=0, column=0, sticky="w", padx=24, pady=(24, 2))

        ctk.CTkLabel(
            card, text=subtitle, font=theme.body_font(12),
            text_color=theme.MUTED,
        ).grid(row=1, column=0, sticky="w", padx=24, pady=(0, 16))

        self.progress = ctk.CTkProgressBar(
            card, mode="indeterminate", height=6, corner_radius=3,
            fg_color=theme.raised_color(), progress_color=theme.ACCENT,
        )
        self.progress.grid(row=2, column=0, sticky="ew", padx=24)
        self.progress.start()

        self.status_label = ctk.CTkLabel(
            card, text=status_text, font=theme.body_font(12),
            text_color=theme.MUTED, wraplength=_WIDTH - 60, justify="left", anchor="w",
        )
        self.status_label.grid(row=3, column=0, sticky="ew", padx=24, pady=(14, 24))

        self._closed = False

    def _center_on_screen(self, y_divisor: int = 2) -> None:
        self.update_idletasks()
        mx, my, mw, mh = _get_monitor_geometry(self)
        x = mx + (mw - _WIDTH) // 2
        y = my + (mh - _HEIGHT) // y_divisor
        self.geometry(f"{_WIDTH}x{_HEIGHT}+{x}+{y}")

    # ------------------------------------------------------------------
    # Потокобезопасный доступ из фонового потока загрузки
    # ------------------------------------------------------------------

    def dispatch(self, fn: Callable[[], None]) -> None:
        if getattr(self, "_closed", True):
            return
        try:
            self.after(0, fn)
        except tk.TclError:
            pass
        except Exception:
            pass

    def set_status(self, text: str) -> None:
        if getattr(self, "_closed", True):
            return
        try:
            self.status_label.configure(text=text[:140])
        except tk.TclError:
            pass
        except Exception:
            pass

    def close(self) -> None:
        if getattr(self, "_closed", False):
            return
        self._closed = True
        try:
            self.progress.stop()
        except Exception:
            pass
        try:
            # Отменяем все висящие after-колбэки (в т.ч. внутренние customtkinter),
            # чтобы после destroy не сыпались "invalid command name".
            pending = self.tk.call("after", "info")
            if isinstance(pending, str):
                pending = pending.split()
            for after_id in pending:
                try:
                    self.after_cancel(after_id)
                except Exception:
                    pass
        except Exception:
            pass
        try:
            self.withdraw()
            self.after_idle(self.destroy)
        except tk.TclError:
            pass
        except Exception:
            try:
                self.destroy()
            except Exception:
                pass


class SplashWindow(ctk.CTk, _ProgressPopupBase):
    """Экран-заставка первого запуска: приложение качает vendor с GitHub.

    Показывается ТОЛЬКО когда файлов нет и их надо скачивать (см. main.py)."""

    def __init__(
        self,
        title: str = "Fuck DPI",
        subtitle: str = "Подготовка приложения к запуску…",
        status_text: str = "Проверка файлов Zapret, TG WS Proxy и Tor…",
    ) -> None:
        super().__init__()

        theme.setup_appearance("dark")

        self.title("Fuck DPI")
        self.geometry(f"{_WIDTH}x{_HEIGHT}")
        self.resizable(False, False)
        self.overrideredirect(True)  # без рамки/заголовка -- чистый сплэш
        self._center_on_screen()

        self._build_ui(title, subtitle, status_text)


class UpdateProgressDialog(ctk.CTkToplevel, _ProgressPopupBase):
    """Окно прогресса обновления поверх главного окна приложения."""

    def __init__(self, master, subtitle: str = "Загружаем новые версии Zapret и TG WS Proxy…") -> None:
        super().__init__(master)

        # Глобальная тема уже настроена главным окном (AppWindow) --
        # CTkToplevel наследует её автоматически.

        self.title("Fuck DPI")
        self.geometry(f"{_WIDTH}x{_HEIGHT}")
        self.resizable(False, False)
        self.overrideredirect(True)

        self._center_on_master()

        self._build_ui("Обновление", subtitle, "Останавливаем Zapret и TG WS Proxy…")

        self.lift()
        self.focus_force()

    def _center_on_master(self) -> None:
        # Для overrideredirect-окон update_idletasks() не гарантирует, что
        # размеры/позиция мастера уже известны -- используем update().
        self.update()
        try:
            px, py = self.master.winfo_rootx(), self.master.winfo_rooty()
            pw, ph = self.master.winfo_width(), self.master.winfo_height()
        except Exception:
            self._center_on_screen(y_divisor=3)
            return
        x = px + max((pw - _WIDTH) // 2, 0)
        y = py + max((ph - _HEIGHT) // 3, 0)
        self.geometry(f"{_WIDTH}x{_HEIGHT}+{x}+{y}")
