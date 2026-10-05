"""Единая палитра, отступы и настройка внешнего вида окна (customtkinter)."""

from __future__ import annotations

import customtkinter as ctk

ACCENT = "#2AABEE"
ACCENT_HOVER = "#1E96D6"
SUCCESS = "#3BA55D"
DANGER = "#ED4245"
MUTED = "#8A8F98"

BG_DARK = "#15171B"
CARD_DARK = "#1E2227"
CARD_DARK_RAISED = "#262B32"
BORDER_DARK = "#2E333B"

BG_LIGHT = "#F5F6F8"
CARD_LIGHT = "#FFFFFF"
CARD_LIGHT_RAISED = "#F0F1F4"
BORDER_LIGHT = "#DEE1E6"

FONT_FAMILY = "Segoe UI"

PAD = 16
GAP = 10


def setup_appearance(mode: str = "dark") -> None:
    ctk.set_appearance_mode(mode if mode in ("dark", "light", "system") else "dark")
    ctk.set_default_color_theme("blue")


def status_color(running: bool) -> str:
    return SUCCESS if running else MUTED


def card_color() -> str:
    return CARD_DARK if ctk.get_appearance_mode() == "Dark" else CARD_LIGHT


def raised_color() -> str:
    return CARD_DARK_RAISED if ctk.get_appearance_mode() == "Dark" else CARD_LIGHT_RAISED


def border_color() -> str:
    return BORDER_DARK if ctk.get_appearance_mode() == "Dark" else BORDER_LIGHT


def heading_font(size: int = 16, weight: str = "bold") -> ctk.CTkFont:
    return ctk.CTkFont(family=FONT_FAMILY, size=size, weight=weight)


def body_font(size: int = 13) -> ctk.CTkFont:
    return ctk.CTkFont(family=FONT_FAMILY, size=size)


def mono_font(size: int = 11) -> ctk.CTkFont:
    return ctk.CTkFont(family="Consolas", size=size)


def make_card(parent, **kwargs) -> ctk.CTkFrame:
    defaults = dict(fg_color=card_color(), corner_radius=14, border_width=1, border_color=border_color())
    defaults.update(kwargs)
    return ctk.CTkFrame(parent, **defaults)


class StatusBadge(ctk.CTkFrame):
    def __init__(self, parent, text: str = "Остановлен", running: bool = False, **kwargs):
        super().__init__(parent, fg_color=raised_color(), corner_radius=999, **kwargs)
        self.grid_columnconfigure(1, weight=0)
        self._dot = ctk.CTkLabel(self, text="●", font=heading_font(13), text_color=status_color(running), width=14)
        self._dot.grid(row=0, column=0, padx=(12, 4), pady=6)
        self._label = ctk.CTkLabel(self, text=text, font=body_font(12))
        self._label.grid(row=0, column=1, padx=(0, 14), pady=6)

    def set_state(self, text: str, running: bool) -> None:
        self._dot.configure(text_color=status_color(running))
        self._label.configure(text=text)
