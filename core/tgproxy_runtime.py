from __future__ import annotations

import asyncio
import logging
import sys
import threading
from pathlib import Path
from typing import Callable, Optional

from config import TGPROXY_DIR

_LOG_FMT = logging.Formatter("%(asctime)s  %(levelname)-5s  %(message)s", datefmt="%H:%M:%S")
_PROXY_LOGGER_NAME = "tg-mtproto-proxy"


def _ensure_importable() -> None:
    path = str(TGPROXY_DIR)
    if path not in sys.path:
        sys.path.insert(0, path)


class TgProxyRuntime:
    def __init__(self, name: str, log_file: Path, on_state_change: Optional[Callable[[str, bool], None]] = None):
        self.name = name
        self.log_file = log_file
        self._on_state_change = on_state_change

        self._op_lock = threading.RLock()      # сериализует start()/stop(), держит только вызывающий поток
        self._state_lock = threading.Lock()    # короткие чтения/записи полей ниже, из любого потока

        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._stop_event: Optional[asyncio.Event] = None
        self._log_handler: Optional[logging.Handler] = None
        self._running = False

        self.on_crash: Optional[Callable[[], None]] = None

    @property
    def is_running(self) -> bool:
        with self._state_lock:
            return self._running

    def start(self, settings) -> None:
        """settings -- core.tgproxy_settings.TgProxySettings."""
        with self._op_lock:
            self.stop()

            ready = threading.Event()
            error_box: dict = {}
            thread = threading.Thread(
                target=self._run, args=(settings, ready, error_box), daemon=True, name="tgproxy-runtime",
            )
            with self._state_lock:
                self._thread = thread
            thread.start()
            ready.wait(timeout=10)

            if "error" in error_box:
                with self._state_lock:
                    if self._thread is thread:
                        self._thread = None
                raise error_box["error"]

        self._set_running(True)

    def stop(self) -> None:
        with self._op_lock:
            with self._state_lock:
                loop, stop_event, thread = self._loop, self._stop_event, self._thread

            if loop is not None and stop_event is not None:
                try:
                    loop.call_soon_threadsafe(stop_event.set)
                except RuntimeError:
                    pass

            if thread is not None and thread.is_alive():
                thread.join(timeout=5)

            with self._state_lock:
                if self._thread is thread:
                    self._thread = None
                    self._loop = None
                    self._stop_event = None

        self._set_running(False)

    # ------------------------------------------------------------------

    def _set_running(self, running: bool) -> None:
        with self._state_lock:
            changed = self._running != running
            self._running = running
        if changed and self._on_state_change:
            try:
                self._on_state_change(self.name, running)
            except Exception:
                pass

    def _attach_logging(self) -> None:
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        from utils.logging_setup import build_log_handler  # vendored helper

        handler = build_log_handler(str(self.log_file), log_max_mb=5, backups=1)
        handler.setFormatter(_LOG_FMT)
        self._log_handler = handler
        logger = logging.getLogger(_PROXY_LOGGER_NAME)
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)
        logging.getLogger("asyncio").setLevel(logging.WARNING)

    def _detach_logging(self) -> None:
        handler = self._log_handler
        if handler is None:
            return
        logging.getLogger(_PROXY_LOGGER_NAME).removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass
        self._log_handler = None

    def _run(self, settings, ready: threading.Event, error_box: dict) -> None:
        _ensure_importable()
        try:
            from proxy import tg_ws_proxy as tgp
            from proxy.config import proxy_config, parse_dc_ip_list
        except Exception as exc:
            error_box["error"] = exc
            ready.set()
            return

        try:
            dc_ips = settings.dc_ips or ["2:149.154.167.220", "4:149.154.167.220"]
            proxy_config.host = settings.host
            proxy_config.port = settings.port
            proxy_config.secret = settings.secret
            proxy_config.dc_redirects = parse_dc_ip_list(dc_ips)
            proxy_config.fake_tls_domain = (settings.fake_tls_domain or "").strip()
        except Exception as exc:
            error_box["error"] = exc
            ready.set()
            return

        self._attach_logging()

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        stop_event = asyncio.Event()
        with self._state_lock:
            self._loop = loop
            self._stop_event = stop_event
        ready.set()

        crashed = False
        try:
            loop.run_until_complete(tgp._run(stop_event))
        except Exception:
            crashed = True
            logging.getLogger(_PROXY_LOGGER_NAME).exception("tg-ws-proxy остановлен из-за ошибки")
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass
            loop.close()
            self._detach_logging()
            with self._state_lock:
                if self._thread is threading.current_thread():
                    self._thread = None
                    self._loop = None
                    self._stop_event = None
            self._set_running(False)
            if crashed and self.on_crash:
                try:
                    self.on_crash()
                except Exception:
                    pass
