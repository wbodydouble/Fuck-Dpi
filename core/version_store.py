from __future__ import annotations

import json
import threading
from dataclasses import dataclass, asdict, fields
from typing import Optional

from config import DATA_DIR

VERSIONS_FILE = DATA_DIR / "versions.json"

_lock = threading.Lock()


@dataclass
class VersionInfo:
    zapret_tag: Optional[str] = None          # например "1.10.0"
    zapret_installed_at: Optional[str] = None  # ISO-дата установки
    tgproxy_sha: Optional[str] = None          # короткий sha коммита main
    tgproxy_installed_at: Optional[str] = None


def load() -> VersionInfo:
    if not VERSIONS_FILE.exists():
        return VersionInfo()
    try:
        raw = json.loads(VERSIONS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return VersionInfo()
    valid = {f.name for f in fields(VersionInfo)}
    filtered = {k: v for k, v in raw.items() if k in valid}
    try:
        return VersionInfo(**filtered)
    except Exception:
        return VersionInfo()


def save(info: VersionInfo) -> None:
    with _lock:
        try:
            VERSIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp = VERSIONS_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(asdict(info), indent=2, ensure_ascii=False), encoding="utf-8")
            tmp.replace(VERSIONS_FILE)
        except Exception:
            pass
