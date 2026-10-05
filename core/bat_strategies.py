from __future__ import annotations

import re
from pathlib import Path
from typing import List, Tuple


_WINWS_MARKER = re.compile(r'winws\.exe"', re.IGNORECASE)


def list_strategy_files(zapret_dir: Path) -> List[Path]:
    return sorted(zapret_dir.glob("general*.bat"))


def strategy_display_name(bat_path: Path) -> str:
    return bat_path.stem


def parse_bat_strategy(
    bat_path: Path,
    bin_dir: Path,
    lists_dir: Path,
    game_filter_tcp: str = "12",
    game_filter_udp: str = "12",
) -> List[str]:
    """Возвращает готовый список аргументов для subprocess.Popen(["winws.exe", *args])."""

    text = bat_path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()

    start_idx = None
    for i, line in enumerate(lines):
        if _WINWS_MARKER.search(line):
            start_idx = i
            break
    if start_idx is None:
        raise ValueError(
            f"Не найдена команда запуска winws.exe в файле {bat_path.name}. "
            f"Возможно, формат стратегии в новой версии репозитория изменился."
        )

    # Склеиваем строку целиком, следуя символам продолжения `^` в конце строк.
    block_lines = []
    i = start_idx
    while True:
        raw_line = lines[i].rstrip("\r\n")
        stripped = raw_line.rstrip()
        continues = stripped.endswith("^")
        if continues:
            stripped = stripped[:-1].rstrip()
        block_lines.append(stripped)
        if not continues:
            break
        i += 1
    full_line = " ".join(block_lines)

    m = _WINWS_MARKER.search(full_line)
    args_str = full_line[m.end():].strip()

    bin_prefix = str(bin_dir.resolve()) + "\\"
    lists_prefix = str(lists_dir.resolve()) + "\\"
    args_str = (
        args_str
        .replace("%BIN%", bin_prefix)
        .replace("%LISTS%", lists_prefix)
        .replace("%GameFilterTCP%", str(game_filter_tcp))
        .replace("%GameFilterUDP%", str(game_filter_udp))
    )

    # Токенизация с учётом кавычек (пути внутри "..." не режем по пробелам).
    raw_tokens = re.findall(r'(?:[^\s"]|"[^"]*")+', args_str)
    tokens = [t.replace('"', "") for t in raw_tokens]

    leftover = [t for t in tokens if "%" in t]
    if leftover:
        raise ValueError(
            f"В стратегии {bat_path.name} остались нераспознанные переменные: {leftover}. "
            f"Проверьте, не появились ли новые %ПЕРЕМЕННЫЕ% в новой версии .bat-файлов."
        )

    return tokens


# ---------------------------------------------------------------------------
# Игровой фильтр (аналог :game_switch_status из service.bat)
# ---------------------------------------------------------------------------

def read_game_filter(flag_file: Path) -> Tuple[str, str, str]:
    """Возвращает (GameFilterTCP, GameFilterUDP, человеко-читаемый статус)."""
    if not flag_file.exists():
        return "12", "12", "выключен"

    content = flag_file.read_text(encoding="utf-8", errors="replace").strip()
    mode = content.splitlines()[0].strip().lower() if content else ""

    if mode == "all":
        return "1024-65535", "1024-65535", "включен (TCP и UDP)"
    if mode == "tcp":
        return "1024-65535", "12", "включен (TCP)"
    # Оригинальный service.bat трактует любое другое непустое значение как UDP-режим
    return "12", "1024-65535", "включен (UDP)"


def set_game_filter(flag_file: Path, mode: str) -> None:
    """mode: 'off' | 'all' | 'tcp' | 'udp'."""
    if mode == "off":
        if flag_file.exists():
            flag_file.unlink()
        return
    flag_file.parent.mkdir(parents=True, exist_ok=True)
    flag_file.write_text(mode, encoding="utf-8")


# ---------------------------------------------------------------------------
# "Пользовательские" списки (аналог :load_user_lists из service.bat)
# ---------------------------------------------------------------------------

_USER_LIST_DEFAULTS = {
    "ipset-exclude-user.txt": "203.0.113.113/32\n",
    "list-general-user.txt": "# Never leave this file empty\ndomain.example.abc\n",
    "list-exclude-user.txt": "domain.example.abc\n",
}


def ensure_user_lists(lists_dir: Path) -> None:
    """Стратегии ссылаются на *-user.txt файлы, которые оригинальный
    service.bat создаёт при первом запуске, если их ещё нет. Без них
    winws.exe получит несуществующий путь в --hostlist/--ipset-exclude.
    """
    lists_dir.mkdir(parents=True, exist_ok=True)
    for name, content in _USER_LIST_DEFAULTS.items():
        p = lists_dir / name
        if not p.exists():
            p.write_text(content, encoding="utf-8")
