"""Sırları .env'den oku (config.yaml'a sır yazmadan).

Öncelik: mevcut ortam değişkeni > Hermes .env dosyası.
Böylece MCP sunucusu `hermes mcp add --env` ile sır taşımak zorunda kalmaz.
"""

from __future__ import annotations

import os
from pathlib import Path

WANTED = (
    "TYPESAFE_API_KEY",
    "TYPESAFE_DEFAULT_MODEL",
    "DEEPSEEK_API_KEY",
    "SYSTEMONE_FALLBACK_BASE_URL",
    "SYSTEMONE_FALLBACK_MODEL",
    "SYSTEMONE_FALLBACK_PRICE_IN_USD_PER_MTOK",
    "SYSTEMONE_FALLBACK_PRICE_OUT_USD_PER_MTOK",
)

_DONE = False


def safe_home() -> Path:
    """Path.home() bazı kısıtlı ortamlarda (USERPROFILE yok) patlar; güvenli sürüm."""
    for var in ("USERPROFILE", "HOME"):
        val = os.environ.get(var)
        if val:
            return Path(val)
    drive, tail = os.environ.get("HOMEDRIVE"), os.environ.get("HOMEPATH")
    if drive and tail:
        return Path(drive + tail)
    try:
        return Path.home()
    except Exception:
        return Path.cwd()


def candidate_env_files() -> list[Path]:
    cands: list[Path] = []
    hh = os.environ.get("HERMES_HOME")
    if hh:
        cands.append(Path(hh) / ".env")
    home = safe_home()
    cands.append(home / ".hermes" / ".env")
    cands.append(home / "AppData" / "Local" / "hermes" / ".env")
    cands.append(Path(__file__).resolve().parents[1] / ".env")
    return cands


def load_env(force: bool = False) -> list[str]:
    """Eksik anahtarları Hermes .env'inden yükler; sırları asla yazdırmaz."""
    global _DONE
    if _DONE and not force:
        return []
    _DONE = True
    loaded: list[str] = []
    for path in candidate_env_files():
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key in WANTED and val and not os.environ.get(key):
                os.environ[key] = val
                loaded.append(key)
    return loaded


def load_env_quiet() -> None:  # import sırasında kullanılır
    try:
        load_env()
    except Exception:
        pass
