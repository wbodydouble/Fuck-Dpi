
from __future__ import annotations
import json,secrets,threading
from dataclasses import dataclass,asdict,field,fields
from typing import Optional

from config import DATA_DIR

FILE = DATA_DIR / "tgproxy_settings.json"

_lock = threading.Lock()


@dataclass
class TgProxySettings:
    host:str='127.0.0.1'
    port:int=1443
    secret:str=field(default_factory=lambda:secrets.token_hex(16))
    dc_ips:Optional[list[str]]=None
    fake_tls_domain:str=''

def _is_valid_secret(secret:str) -> bool:
    if not secret or len(secret) != 32:
        return False
    try:
        bytes.fromhex(secret)
        return True
    except ValueError:
        return False

def load_settings():
    if not FILE.exists():
        s=TgProxySettings()
        save_settings(s)
        return s
    try:
        raw=json.loads(FILE.read_text(encoding='utf-8'))
    except Exception:
        s=TgProxySettings()
        save_settings(s)
        return s
    valid={f.name for f in fields(TgProxySettings)}
    filtered={k:v for k,v in raw.items() if k in valid}
    try:
        s=TgProxySettings(**filtered)
    except Exception:
        return TgProxySettings()
    if not _is_valid_secret(s.secret):
        s.secret=secrets.token_hex(16)
        save_settings(s)
    return s

def save_settings(s):
    with _lock:
        try:
            FILE.parent.mkdir(parents=True,exist_ok=True)
            tmp=FILE.with_suffix('.tmp')
            tmp.write_text(json.dumps(asdict(s),indent=2,ensure_ascii=False),encoding='utf-8')
            tmp.replace(FILE)
        except Exception:
            pass
