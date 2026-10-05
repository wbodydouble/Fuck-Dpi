from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import tarfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse

import requests

from core import version_store

ProgressCb = Optional[Callable[[str], None]]

_CREATIONFLAGS = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


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
TOR_DIR = VENDOR_DIR / "tor"

ZAPRET_REPO = "Flowseal/zapret-discord-youtube"
TGPROXY_REPO = "Flowseal/tg-ws-proxy"

_FALLBACK_ZAPRET_TAG = "1.10.0"
_FALLBACK_ZAPRET_URL = (
    "https://github.com/Flowseal/zapret-discord-youtube/releases/download/"
    "1.10.0/zapret-discord-youtube-1.10.0.zip"
)
_FALLBACK_TGPROXY_URL = "https://github.com/Flowseal/tg-ws-proxy/archive/refs/heads/main.zip"

# Tor Expert Bundle — захардкоженная стабильная версия (обновления не требуются).
_TOR_BUNDLE_VERSION = "15.0.24"
_TOR_MIRRORS = [
    # Основные зеркала Tor Project. В некоторых странах (например, РФ)
    # официальные домены блокируются по DNS, поэтому перебираем несколько.
    f"https://dist.torproject.org/torbrowser/{_TOR_BUNDLE_VERSION}/"
    f"tor-expert-bundle-windows-x86_64-{_TOR_BUNDLE_VERSION}.tar.gz",
    f"https://tor.ybti.net/dist/torbrowser/{_TOR_BUNDLE_VERSION}/"
    f"tor-expert-bundle-windows-x86_64-{_TOR_BUNDLE_VERSION}.tar.gz",
    f"https://archive.torproject.org/tor-package-archive/torbrowser/{_TOR_BUNDLE_VERSION}/"
    f"tor-expert-bundle-windows-x86_64-{_TOR_BUNDLE_VERSION}.tar.gz",
]

TEMP_ZAPRET = VENDOR_DIR / "zapret-temp.zip"
TEMP_TGPROXY = VENDOR_DIR / "tgproxy-temp.zip"
TEMP_TOR = VENDOR_DIR / "tor-temp.tar.gz"

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

def _download_file(url: str, dest: Path, progress_cb: ProgressCb = None, label: str = "", max_retries: int = 3) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    last_error: Optional[Exception] = None

    for attempt in range(1, max_retries + 1):
        _report(progress_cb, f"⬇️  Скачивание {label or url}…" + (f" (попытка {attempt}/{max_retries})" if attempt > 1 else ""))
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
            return
        except requests.exceptions.RequestException as e:
            last_error = e
            _report(progress_cb, f"⚠️  Ошибка скачивания {label} (попытка {attempt}/{max_retries}): {e}")
            if attempt < max_retries:
                time.sleep(1.5 * attempt)

    _report(progress_cb, f"❌ Ошибка скачивания {label}: {last_error}")
    raise last_error if last_error else RuntimeError(f"Не удалось скачать {label or url}")


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


def _remove_existing(path: Path) -> None:
    """Удаляет файл или папку, если существует."""
    if not path.exists():
        return
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def _flatten_extracted_folder_tor(source_dir: Path, target_dir: Path, progress_cb: ProgressCb = None) -> None:
    """Переносит содержимое распакованного Tor Expert Bundle в target_dir.

    Архив может быть двух видов:
      1) с одной корневой папкой, внутри которой лежат data/, docs/, tor/;
      2) без корневой папки: data/, docs/, tor/ прямо в корне архива.
    В обоих случаях tor.exe находится в папке tor/. Перемещаем всё из папки
    tor/ на уровень target_dir, а data/ и docs/ оставляем рядом с tor.exe.
    """
    target_dir.mkdir(parents=True, exist_ok=True)

    # Ищем папку, содержащую tor.exe. Это может быть source_dir/tor
    # или source_dir/<корневая-папка>/tor.
    tor_exe_candidates = list(source_dir.rglob("tor.exe"))
    if not tor_exe_candidates:
        raise RuntimeError("tor.exe не найден в распакованном архиве")

    # Берём ближайшую к корню папку с tor.exe.
    tor_exe = min(tor_exe_candidates, key=lambda p: len(p.parts))
    tor_folder = tor_exe.parent

    # Переносим содержимое папки tor (tor.exe, pluggable_transports и т.д.)
    # на уровень target_dir.
    for item in tor_folder.iterdir():
        dst = target_dir / item.name
        _remove_existing(dst)
        shutil.move(str(item), str(dst))
        _report(progress_cb, f"✅ Tor: {item.name}")

    # Удаляем опустевшую папку tor, если она не совпадает с source_dir.
    if tor_folder != source_dir:
        try:
            tor_folder.rmdir()
        except OSError:
            pass

    # Переносим остальные корневые элементы (data, docs и т.д.)
    # на уровень target_dir.
    source_root = tor_folder.parent if tor_folder != source_dir else source_dir
    for item in source_root.iterdir():
        if item == tor_folder:
            continue
        dst = target_dir / item.name
        _remove_existing(dst)
        shutil.move(str(item), str(dst))
        _report(progress_cb, f"✅ Tor: {item.name}")


def _clean_temp_files() -> None:
    for p in [TEMP_ZAPRET, TEMP_TGPROXY, TEMP_TOR]:
        if p.exists():
            p.unlink()
            print(f"🧹 Удалён временный файл: {p.name}")


# ---------------------------------------------------------------------------
# Надёжное удаление vendor-папок
#
# Наивный shutil.rmtree здесь ломается с «Отказано в доступе», потому что:
#   * winws.exe ещё не до конца завершился и держит свои файлы;
#   * где-то висит осиротевший/запущенный вручную winws.exe из vendor/zapret;
#   * драйвер WinDivert отпускает свой .sys через пару секунд после выхода;
#   * у распакованных из zip файлов бывает атрибут read-only.
# ---------------------------------------------------------------------------

def vendor_ready() -> bool:
    """True, если файлы Zapret и TG WS Proxy уже установлены.

    Tor считается опциональным: если его не удалось скачать (например,
    из-за блокировки dist.torproject.org), приложение продолжает работу
    с Zapret и TG WS Proxy.
    """
    zapret_exe, tgproxy_ok, _tor_ok = _find_required_files()
    return bool(zapret_exe) and bool(tgproxy_ok)


def kill_stale_zapret_processes(progress_cb: ProgressCb = None) -> None:
    """Останавливает winws.exe, запущенные ИЗ нашей vendor/zapret -- включая
    зависшие после сбоя или запущенные вручную через .bat, которые приложению
    не известны. Чужие winws.exe (из других папок) не трогаем.

    Best-effort шаг: любые ошибки глотаются -- из-за проверки зависших
    процессов обновление падать не должно."""
    if sys.platform != "win32":
        return
    # Вывод PowerShell пишем во временный файл, а НЕ через пайп: захват
    # вывода через stdio может быть запрещён политикой безопасности среды.
    tmp_path = VENDOR_DIR / f"_winws_list_{os.getpid()}.tmp"
    try:
        VENDOR_DIR.mkdir(parents=True, exist_ok=True)
        ps_script = (
            "Get-CimInstance Win32_Process -Filter \"Name='winws.exe'\" | "
            "ForEach-Object { \"$($_.ProcessId)|$($_.ExecutablePath)\" } | "
            f"Out-File -FilePath '{tmp_path}' -Encoding utf8"
        )
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=20, creationflags=_CREATIONFLAGS,
        )
        try:
            content = tmp_path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            return
        prefix = str(ZAPRET_DIR).rstrip("\\").lower()
        for line in content.splitlines():
            line = line.strip()
            pid_s, sep, exe_path = line.partition("|")
            if not sep or not pid_s.strip().isdigit():
                continue
            if exe_path.strip().lower().startswith(prefix):
                _report(progress_cb, f"🛑 Останавливаем зависший процесс winws.exe (PID {pid_s.strip()})")
                subprocess.run(
                    ["taskkill", "/F", "/PID", pid_s.strip(), "/T"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=_CREATIONFLAGS,
                )
    except Exception as e:
        try:
            print(f"[warn] Не удалось проверить зависшие процессы winws.exe: {e}")
        except Exception:
            pass
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass


def _extract_tar_gz(tar_path: Path, extract_to: Path, progress_cb: ProgressCb = None) -> None:
    _report(progress_cb, f"📦 Распаковка {tar_path.name}…")
    extract_to.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tar_path, "r:gz") as tf:
        tf.extractall(extract_to)


def _unload_windivert_driver(progress_cb: ProgressCb = None) -> None:
    """Выгружает драйвер WinDivert из ядра Windows.

    winws.exe загружает WinDivert.sys; даже после kill'а процесса драйвер
    может оставаться в памяти ядра и держать свой .sys файл несколько секунд.
    Мы ищем сервисы с именем, содержащим "WinDivert", и останавливаем их.
    """
    if sys.platform != "win32":
        return
    _report(progress_cb, "🛑 Остановка драйвера WinDivert…")
    try:
        ps_script = (
            "Get-CimInstance Win32_SystemDriver | Where-Object {$_.Name -like '*WinDivert*'} | "
            "ForEach-Object { $name = $_.Name; & sc.exe stop $name | Out-Null }"
        )
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=15, creationflags=_CREATIONFLAGS,
        )
    except Exception as e:
        try:
            print(f"[warn] Не удалось остановить WinDivert: {e}")
        except Exception:
            pass


def _schedule_delete_on_reboot(path: Path) -> bool:
    """Помечает файл для удаления при следующей перезагрузке Windows.

    Требуются права администратора. Если не удалось — возвращает False.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        MOVEFILE_DELAY_UNTIL_REBOOT = 0x4
        kernel32 = ctypes.windll.kernel32
        kernel32.MoveFileExW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
        kernel32.MoveFileExW.restype = ctypes.c_bool
        return bool(kernel32.MoveFileExW(str(path), None, MOVEFILE_DELAY_UNTIL_REBOOT))
    except Exception:
        return False


def _try_rename_locked_files(path: Path, progress_cb: ProgressCb = None) -> None:
    """Хирургически переименовывает файлы, которые не удаётся удалить.

    WinDivert.sys и прочие залоченные драйверные файлы иногда нельзя удалить,
    но можно переименовать, освободив место для распаковки новой версии.
    """
    try:
        entries = list(path.rglob("*"))
    except OSError:
        return
    for p in reversed(entries):
        if not p.is_file():
            continue
        try:
            p.unlink()
        except OSError:
            try:
                new_name = p.with_name(p.name + ".old")
                if new_name.exists():
                    new_name = p.with_name(f"{p.name}.old.{os.getpid()}")
                p.rename(new_name)
                _report(progress_cb, f"📝 Переименован залоченный файл: {p.name}")
            except OSError:
                pass


def kill_stale_tor_processes(progress_cb: ProgressCb = None) -> None:
    """Останавливает tor.exe, запущенные ИЗ нашей vendor/tor."""
    if sys.platform != "win32":
        return
    tmp_path = VENDOR_DIR / f"_tor_list_{os.getpid()}.tmp"
    try:
        VENDOR_DIR.mkdir(parents=True, exist_ok=True)
        ps_script = (
            "Get-CimInstance Win32_Process -Filter \"Name='tor.exe'\" | "
            "ForEach-Object { \"$($_.ProcessId)|$($_.ExecutablePath)\" } | "
            f"Out-File -FilePath '{tmp_path}' -Encoding utf8"
        )
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=20, creationflags=_CREATIONFLAGS,
        )
        try:
            content = tmp_path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            return
        prefix = str(TOR_DIR).rstrip("\\").lower()
        for line in content.splitlines():
            line = line.strip()
            pid_s, sep, exe_path = line.partition("|")
            if not sep or not pid_s.strip().isdigit():
                continue
            if exe_path.strip().lower().startswith(prefix):
                _report(progress_cb, f"🛑 Останавливаем зависший процесс tor.exe (PID {pid_s.strip()})")
                subprocess.run(
                    ["taskkill", "/F", "/PID", pid_s.strip(), "/T"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=_CREATIONFLAGS,
                )
    except Exception as e:
        try:
            print(f"[warn] Не удалось проверить зависшие процессы tor.exe: {e}")
        except Exception:
            pass
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass


def _clear_readonly(path: Path) -> None:
    """Снимает read-only со всех файлов и папок внутри path (и с самой path):
    shutil.rmtree не удаляет такие объекты и падает с 'Отказано в доступе'."""
    try:
        entries = [path, *path.rglob("*")]
    except OSError:
        return
    for p in reversed(entries):  # сначала вглубь
        try:
            p.chmod(stat.S_IWRITE | stat.S_IEXEC if p.is_dir() else stat.S_IWRITE)
        except OSError:
            pass


def remove_dir_forcefully(path: Path, progress_cb: ProgressCb = None, attempts: int = 12) -> None:
    """Удаляет папку максимально настойчиво: снимает read-only, при повторных
    неудачах останавливает winws.exe и драйвер WinDivert, переименовывает
    залоченные файлы и делает несколько попыток с паузами.

    Драйвер WinDivert может отпускать свои файлы ещё несколько секунд после
    выхода winws.exe, поэтому задержки между попытками увеличены.
    """
    if not path.exists():
        return
    last_error: Optional[Exception] = None
    for attempt in range(1, attempts + 1):
        _clear_readonly(path)
        if attempt == 2:
            kill_stale_zapret_processes(progress_cb)
            _unload_windivert_driver(progress_cb)
        if attempt >= 4:
            _try_rename_locked_files(path, progress_cb)
        try:
            shutil.rmtree(path)
            return
        except OSError as e:
            last_error = e
            if attempt < attempts:
                time.sleep(min(0.8 * attempt, 3.0))

    # Последняя мера: если папку нельзя удалить целиком, переименовываем её
    # в .old и планируем удаление оставшихся файлов при перезагрузке.
    _try_move_aside_and_schedule(path, progress_cb)

    # Если удалось отодвинуть папку в сторону — считаем задачу выполненной.
    if not path.exists():
        return

    raise RuntimeError(
        f"Не удалось удалить папку {path}: файл занят другим процессом.\n"
        f"Последняя ошибка: {last_error}\n\n"
        f"Закройте winws.exe вручную через Диспетчер задач (или добавьте папку "
        f"приложения в исключения антивируса) и нажмите «Обновить» ещё раз."
    )


def _try_move_aside_and_schedule(path: Path, progress_cb: ProgressCb = None) -> None:
    """Переименовывает неудаляемую папку и планирует её содержимое на удаление
    при следующей перезагрузке Windows."""
    if not path.exists():
        return
    backup = path.parent / f"{path.name}.old.{os.getpid()}"
    try:
        path.rename(backup)
        _report(progress_cb, f"📝 Залоченная папка отложена: {backup.name}")
    except OSError:
        # Переименование самой папки тоже не получилось — пробуем по файлам.
        _try_rename_locked_files(path, progress_cb)
        return
    for f in backup.rglob("*"):
        if f.is_file():
            _schedule_delete_on_reboot(f)


def _fetch_vendor(progress_cb: ProgressCb = None, include_tor: bool = True) -> None:
    _report(progress_cb, "🚀 Загрузка зависимостей для Zapret + TG WS Proxy" + (" + Tor" if include_tor else ""))

    remove_dir_forcefully(ZAPRET_DIR, progress_cb)
    remove_dir_forcefully(TGPROXY_DIR, progress_cb)
    # Tor удаляем только после успешного скачивания, чтобы при обрыве
    # соединения сохранилась предыдущая (возможно, рабочая) копия.

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

    # ---- Tor ----
    tor_downloaded = False
    if include_tor:
        last_tor_error: Optional[Exception] = None
        for tor_url in _TOR_MIRRORS:
            try:
                host = urlparse(tor_url).netloc
                _report(progress_cb, f"ℹ️  Пробуем зеркало Tor: {host}")
                _download_file(tor_url, TEMP_TOR, progress_cb, label=f"Tor {_TOR_BUNDLE_VERSION}")
                remove_dir_forcefully(TOR_DIR, progress_cb)
                tor_extract = VENDOR_DIR / "tor-temp-extract"
                _extract_tar_gz(TEMP_TOR, tor_extract, progress_cb)
                _flatten_extracted_folder_tor(tor_extract, TOR_DIR, progress_cb)
                if tor_extract.exists():
                    shutil.rmtree(tor_extract)
                    _report(progress_cb, "🧹 Временная папка Tor удалена")
                tor_downloaded = True
                break
            except Exception as e:
                last_tor_error = e
                _report(progress_cb, f"⚠️  Не удалось скачать Tor с {tor_url}: {e}")
                # Удаляем битый временный файл, если он остался.
                if TEMP_TOR.exists():
                    try:
                        TEMP_TOR.unlink()
                    except OSError:
                        pass

        if not tor_downloaded:
            _report(progress_cb, f"⚠️  Не удалось скачать Tor: {last_tor_error}. Продолжаем без Tor.")

    _clean_temp_files()

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    version_store.save(version_store.VersionInfo(
        zapret_tag=zapret_tag,
        zapret_installed_at=now,
        tgproxy_sha=tgproxy_sha,
        tgproxy_installed_at=now,
    ))

    if tor_downloaded or not include_tor:
        _report(progress_cb, "🎉 Готово! Все зависимости скачаны и разложены по папкам.")
    else:
        _report(progress_cb, "🎉 Готово! Zapret и TG WS Proxy скачаны. Tor будет загружен при следующем запуске.")


def _find_required_files() -> tuple[Path | None, bool, bool]:
    """
    Ищет:
      - Zapret: winws.exe внутри bin/ (или любой winws*.exe)
      - TG Proxy: наличие папки proxy и файла tg_ws_proxy.py в ней
      - Tor: tor.exe в vendor/tor/
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

    tor_exe = TOR_DIR / "tor.exe"
    tor_ok = tor_exe.exists()
    return zapret_exe, tgproxy_ok, tor_ok


def ensure_vendor(progress_cb: ProgressCb = None) -> bool:
    """
    Проверяет наличие зависимостей и при необходимости скачивает их.
    Возвращает True, если готовы Zapret и TG WS Proxy. Tor опционален:
    если скачать не удалось, приложение продолжает работу без него.
    """
    zapret_exe, tgproxy_ok, tor_ok = _find_required_files()

    if zapret_exe and tgproxy_ok and tor_ok:
        _report(progress_cb, "✅ Все зависимости уже присутствуют.")
        return True

    _report(progress_cb, "⚠️  Некоторые файлы отсутствуют. Начинаем загрузку…")
    _fetch_vendor(progress_cb, include_tor=not tor_ok)

    zapret_exe, tgproxy_ok, tor_ok = _find_required_files()
    if zapret_exe and tgproxy_ok:
        if tor_ok:
            _report(progress_cb, "✅ После загрузки файлы найдены.")
        else:
            _report(progress_cb, "✅ Zapret и TG WS Proxy готовы. Tor будет загружен при следующей возможности.")
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
    заново. Tor не обновляется (используется захардкоженная стабильная версия).
    Вызывающий код (GUI) отвечает за остановку процессов ДО вызова и
    перезапуск приложения ПОСЛЕ успешного завершения.
    """
    _report(progress_cb, "🔄 Удаление старых файлов…")
    remove_dir_forcefully(ZAPRET_DIR, progress_cb)
    remove_dir_forcefully(TGPROXY_DIR, progress_cb)

    _fetch_vendor(progress_cb, include_tor=False)

    zapret_exe, tgproxy_ok, tor_ok = _find_required_files()
    if not (zapret_exe and tgproxy_ok):
        raise RuntimeError("После обновления не удалось найти необходимые файлы")
    return True
