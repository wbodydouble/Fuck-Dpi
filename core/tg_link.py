from __future__ import annotations

import os
import subprocess
import sys
import urllib.parse

from core.tgproxy_settings import load_settings


def _link_host(host: str) -> str:
    if host != "0.0.0.0":
        return host
    try:
        import socket

        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def build_proxy_link() -> str:
    s = load_settings()
    tls_domain = (s.fake_tls_domain or "").strip()
    if tls_domain:
        secret = "ee" + s.secret + tls_domain.encode("ascii").hex()
    else:
        secret = "dd" + s.secret
    params = {"server": _link_host(s.host), "port": s.port, "secret": secret}
    return "tg://proxy?" + urllib.parse.urlencode(params, safe="")


def open_in_telegram() -> None:
    link = build_proxy_link()
    if sys.platform.startswith("win"):
        os.startfile(link)
    elif sys.platform == "darwin":
        subprocess.run(["open", link], check=False)
    else:
        subprocess.run(["xdg-open", link], check=False)
