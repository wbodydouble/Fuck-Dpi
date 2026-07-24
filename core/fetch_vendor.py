from __future__ import annotations

import shutil
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import requests

from core import version_store

ProgressCb = Optional[Callable[[str], None]]


def _report(cb: ProgressCb, message: str) -> None:
    print(message)
    if cb:
        try:
            cb(message)
        except Exception:
            pass


# Определяем корневую папку приложения
def _get_root_dir() -> Path:
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    else:
        return Path(__file__).resolve().parent.parent

ROOT_DIR = _get_root_dir()
VENDOR_DIR = ROOT_DIR / "vendor"
ZAPRET_DIR = VENDOR_DIR / "zapret"
TGPROXY_DIR = VENDOR_DIR / "tgproxy"

ZAPRET_REPO = "Flowseal/zapret-discord-youtube"
TGPROXY_REPO = "Flowseal/tg-ws-proxy"

_FALLBACK_ZAPRET_TAG = "1.10.0"
_FALLBACK_ZAPRET_URL = (
    "https://github.com/Flowseal/zapret-discord-youtube/releases/download/"
    "1.10.0/zapret-discord-youtube-1.10.0.zip"
)
_FALLBACK_TGPROXY_URL = "https://github.com/Flowseal/tg-ws-proxy/archive/refs/heads/main.zip"

TEMP_ZAPRET = VENDOR_DIR / "zapret-temp.zip"
TEMP_TGPROXY = VENDOR_DIR / "tgproxy-temp.zip"

_API_HEADERS = {"Accept": "application/vnd.github+json", "User-Agent": "zapret-tg-tray"}


# ---------------------------------------------------------------------------
# Определение последних доступных версий через GitHub API
# ---------------------------------------------------------------------------

def get_latest_zapret_release() -> tuple[str, str]:
    """Возвращает (tag_name, download_url_первого_zip_asset'а) последнего релиза."""
    try:
        resp = requests.get(
            f"https://api.github.com/repos/{ZAPRET_REPO}/releases/latest",
            headers=_API_HEADERS, timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        tag = data.get("tag_name")
        assets = data.get("assets") or []
        zip_asset = next((a for a in assets if a.get("name", "").endswith(".zip")), None)
        if tag and zip_asset:
            return tag, zip_asset["browser_download_url"]
    except Exception as e:
        print(f"⚠️  Не удалось получить последний релиз zapret с GitHub: {e}")
    return _FALLBACK_ZAPRET_TAG, _FALLBACK_ZAPRET_URL


def get_latest_tgproxy_sha() -> Optional[str]:
    """Возвращает sha последнего коммита ветки main репозитория tg-ws-proxy."""
    try:
        resp = requests.get(
            f"https://api.github.com/repos/{TGPROXY_REPO}/commits/main",
            headers=_API_HEADERS, timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("sha")
    except Exception as e:
        print(f"⚠️  Не удалось получить последний коммит tg-ws-proxy с GitHub: {e}")
        return None


# ---------------------------------------------------------------------------
# Скачивание / распаковка
# ---------------------------------------------------------------------------

def _download_file(url: str, dest: Path, progress_cb: ProgressCb = None, label: str = "") -> None:
    _report(progress_cb, f"⬇️  Скачивание {label or url}…")
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        response = requests.get(url, stream=True, timeout=60)
        response.raise_for_status()
        total = int(response.headers.get("content-length") or 0)
        downloaded = 0
        last_pct = -1
        with open(dest, "wb") as f:
            for chunk in response.iter_content(chunk_size=65536):
                if not chunk:
                    continue
                f.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = int(downloaded * 100 / total)
                    if pct != last_pct:
                        last_pct = pct
                        _report(progress_cb, f"⬇️  {label or dest.name}: {pct}% ({downloaded // 1024} КБ / {total // 1024} КБ)")
        _report(progress_cb, f"✅ Скачано: {dest.name}")
    except requests.exceptions.RequestException as e:
        _report(progress_cb, f"❌ Ошибка скачивания {label}: {e}")
        raise


def _extract_zip(zip_path: Path, extract_to: Path, progress_cb: ProgressCb = None) -> None:
    _report(progress_cb, f"📦 Распаковка {zip_path.name}…")
    extract_to.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(extract_to)


def _flatten_extracted_folder(target_dir: Path) -> None:
    """Если внутри target_dir есть одна папка, перемещаем её содержимое наверх."""
    items = list(target_dir.iterdir())
    if len(items) == 1 and items[0].is_dir():
        inner = items[0]
        print(f"📁 Уплощение {inner} -> {target_dir}")
        for item in inner.iterdir():
            shutil.move(str(item), str(target_dir / item.name))
        inner.rmdir()
        print("✅ Уплощение выполнено")
    else:
        # Если папок несколько, возможно, там уже есть старая вложенная папка
        for item in target_dir.iterdir():
            if item.is_dir() and item.name.startswith("zapret-discord-youtube"):
                print(f"📁 Найдена старая папка {item.name}, переносим содержимое")
                for sub in item.iterdir():
                    shutil.move(str(sub), str(target_dir / sub.name))
                item.rmdir()
                print("✅ Старая папка убрана")
                break


def _clean_temp_files() -> None:
    for p in [TEMP_ZAPRET, TEMP_TGPROXY]:
        if p.exists():
            p.unlink()
            print(f"🧹 Удалён временный файл: {p.name}")


def _fetch_vendor(progress_cb: ProgressCb = None) -> None:
    _report(progress_cb, "🚀 Загрузка зависимостей для Zapret + TG WS Proxy")

    if ZAPRET_DIR.exists():
        shutil.rmtree(ZAPRET_DIR)
    if TGPROXY_DIR.exists():
        shutil.rmtree(TGPROXY_DIR)

    # ---- Zapret ----
    zapret_tag, zapret_url = get_latest_zapret_release()
    _report(progress_cb, f"ℹ️  Последняя версия Zapret: {zapret_tag}")
    _download_file(zapret_url, TEMP_ZAPRET, progress_cb, label=f"Zapret {zapret_tag}")
    _extract_zip(TEMP_ZAPRET, ZAPRET_DIR, progress_cb)
    _flatten_extracted_folder(ZAPRET_DIR)

    # ---- TG WS Proxy ----
    tgproxy_sha = get_latest_tgproxy_sha()
    _report(progress_cb, f"ℹ️  Последний коммит TG WS Proxy: {(tgproxy_sha or '?')[:7]}")
    _download_file(_FALLBACK_TGPROXY_URL, TEMP_TGPROXY, progress_cb, label="TG WS Proxy")
    temp_extract = VENDOR_DIR / "tgproxy-temp-extract"
    _extract_zip(TEMP_TGPROXY, temp_extract, progress_cb)
    source = temp_extract / "tg-ws-proxy-main"
    if source.exists():
        TGPROXY_DIR.mkdir(parents=True, exist_ok=True)
        for needed in ["proxy", "utils"]:
            src = source / needed
            if src.exists():
                dst = TGPROXY_DIR / needed
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.copytree(src, dst)
                _report(progress_cb, f"✅ Скопирована папка {needed}")
        shutil.rmtree(temp_extract)
        print("🧹 Временная папка удалена")
    else:
        _report(progress_cb, "❌ Не найдена папка tg-ws-proxy-main в архиве")
        raise RuntimeError("Не найдена папка tg-ws-proxy-main в скачанном архиве")

    _clean_temp_files()

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    version_store.save(version_store.VersionInfo(
        zapret_tag=zapret_tag,
        zapret_installed_at=now,
        tgproxy_sha=tgproxy_sha,
        tgproxy_installed_at=now,
    ))

    _report(progress_cb, "🎉 Готово! Все зависимости скачаны и разложены по папкам.")


def _find_required_files() -> tuple[Path | None, bool]:
    """
    Ищет:
      - Zapret: winws.exe внутри bin/ (или любой winws*.exe)
      - TG Proxy: наличие папки proxy и файла tg_ws_proxy.py в ней
    """
    zapret_exe = None
    bin_dir = ZAPRET_DIR / "bin"
    if bin_dir.exists():
        for exe in bin_dir.glob("*.exe"):
            if "winws" in exe.name.lower():
                zapret_exe = exe
                break
    if zapret_exe is None:
        for exe in ZAPRET_DIR.rglob("*.exe"):
            if "winws" in exe.name.lower():
                zapret_exe = exe
                break

    proxy_dir = TGPROXY_DIR / "proxy"
    tgproxy_ok = proxy_dir.exists() and (proxy_dir / "tg_ws_proxy.py").exists()
    return zapret_exe, tgproxy_ok


def ensure_vendor(progress_cb: ProgressCb = None) -> bool:
    """
    Проверяет наличие зависимостей и при необходимости скачивает их.
    Возвращает True, если всё готово.
    """
    zapret_exe, tgproxy_ok = _find_required_files()

    if zapret_exe and tgproxy_ok:
        _report(progress_cb, "✅ Все зависимости уже присутствуют.")
        return True

    _report(progress_cb, "⚠️  Некоторые файлы отсутствуют. Начинаем загрузку с GitHub…")
    _fetch_vendor(progress_cb)

    zapret_exe, tgproxy_ok = _find_required_files()
    if zapret_exe and tgproxy_ok:
        _report(progress_cb, "✅ После загрузки файлы найдены.")
        return True
    else:
        _report(progress_cb, "❌ После загрузки файлы всё равно не найдены. Проверьте структуру архивов.")
        return False


# ---------------------------------------------------------------------------
# Проверка и выполнение обновлений
# ---------------------------------------------------------------------------

def check_for_updates() -> dict:
    """
    Сравнивает установленные версии (core/version_store.py) с последними
    доступными на GitHub. Не скачивает ничего сама - только проверяет.

    Возвращает словарь:
        {
            "zapret_current": str | None,
            "zapret_latest": str | None,
            "tgproxy_current": str | None,
            "tgproxy_latest": str | None,
            "update_available": bool,
            "error": str | None,
        }
    """
    installed = version_store.load()
    result = {
        "zapret_current": installed.zapret_tag,
        "zapret_latest": None,
        "tgproxy_current": installed.tgproxy_sha,
        "tgproxy_latest": None,
        "update_available": False,
        "error": None,
    }
    try:
        zapret_tag, _ = get_latest_zapret_release()
        tgproxy_sha = get_latest_tgproxy_sha()
        result["zapret_latest"] = zapret_tag
        result["tgproxy_latest"] = tgproxy_sha

        zapret_stale = bool(installed.zapret_tag) and zapret_tag and installed.zapret_tag != zapret_tag
        tgproxy_stale = bool(installed.tgproxy_sha) and tgproxy_sha and installed.tgproxy_sha != tgproxy_sha
        if installed.zapret_tag is None or installed.tgproxy_sha is None:
            version_store.save(version_store.VersionInfo(
                zapret_tag=installed.zapret_tag or zapret_tag,
                zapret_installed_at=installed.zapret_installed_at,
                tgproxy_sha=installed.tgproxy_sha or tgproxy_sha,
                tgproxy_installed_at=installed.tgproxy_installed_at,
            ))
        else:
            result["update_available"] = zapret_stale or tgproxy_stale
    except Exception as e:
        result["error"] = str(e)
    return result


def perform_update(progress_cb: ProgressCb = None) -> bool:
    """
    Удаляет старые vendor/zapret и vendor/tgproxy и качает актуальные версии
    заново. Вызывающий код (GUI) отвечает за остановку процессов ДО вызова и
    перезапуск приложения ПОСЛЕ успешного завершения.
    """
    _report(progress_cb, "🔄 Удаление старых файлов…")
    if ZAPRET_DIR.exists():
        shutil.rmtree(ZAPRET_DIR, ignore_errors=True)
    if TGPROXY_DIR.exists():
        shutil.rmtree(TGPROXY_DIR, ignore_errors=True)

    _fetch_vendor(progress_cb)

    zapret_exe, tgproxy_ok = _find_required_files()
    if not (zapret_exe and tgproxy_ok):
        raise RuntimeError("После обновления не удалось найти необходимые файлы")
    return True
