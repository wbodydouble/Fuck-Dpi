import sys
from pathlib import Path


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))

if _is_frozen():
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent

if _is_frozen() and hasattr(sys, "_MEIPASS"):
    _BUNDLE_DIR = Path(sys._MEIPASS)  # type: ignore[attr-defined]
else:
    _BUNDLE_DIR = Path(__file__).resolve().parent

# ---------- Zapret ----------
ZAPRET_DIR = BASE_DIR / "vendor" / "zapret"
ZAPRET_BIN_DIR = ZAPRET_DIR / "bin"
ZAPRET_LISTS_DIR = ZAPRET_DIR / "lists"
ZAPRET_UTILS_DIR = ZAPRET_DIR / "utils"
WINWS_EXE = ZAPRET_BIN_DIR / "winws.exe"
GAME_FILTER_FLAG_FILE = ZAPRET_UTILS_DIR / "game_filter.enabled"

# ---------- TG WS Proxy ----------
TGPROXY_DIR = BASE_DIR / "vendor" / "tgproxy"
TGPROXY_SRC_DIR = TGPROXY_DIR
TGPROXY_DEFAULT_PORT = 1443

# ---------- Логи ----------
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

ZAPRET_LOG = LOG_DIR / "zapret.log"
TGPROXY_LOG = LOG_DIR / "tgproxy.log"

# ---------- Данные (настройки, сохранённое состояние) ----------
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

# ---------- Иконки ----------
ICON_PATH = _BUNDLE_DIR / "assets" / "icon.png"
ICON_ICO_PATH = _BUNDLE_DIR / "assets" / "icon.ico"
