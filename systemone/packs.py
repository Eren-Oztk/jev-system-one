"""Karar pack'leri: sorular ve eşikler tek yerde dursun (TypeSafe önerisi).

Pack = sabit soru seti + eşikler + politika kuralları. Kod sadece cevapları
tüketir; soruyu değiştirmek için kodu değil pack'i düzenlersin.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .env import safe_home

BUILTIN_DIR = Path(__file__).parent / "packs"
USER_DIR = safe_home() / ".system-one" / "packs"


def search_dirs() -> list[Path]:
    return [BUILTIN_DIR, USER_DIR]


def list_packs() -> dict[str, Path]:
    """Aynı isimde pack varsa kullanıcı dizini builtin'i gölgeler (son yazan kazanır)."""
    found: dict[str, Path] = {}
    for d in search_dirs():
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.json")):
            found[f.stem] = f
    return found


def load_pack(name_or_path: str) -> dict[str, Any]:
    p = Path(name_or_path)
    if not p.exists():
        packs = list_packs()
        if name_or_path not in packs:
            raise FileNotFoundError(
                f"Pack bulunamadı: {name_or_path}. Mevcut: {', '.join(sorted(packs))}"
            )
        p = packs[name_or_path]
    data = json.loads(p.read_text(encoding="utf-8"))
    if "questions" not in data or not isinstance(data["questions"], dict):
        raise ValueError(f"{p}: 'questions' haritası zorunlu.")
    data.setdefault("name", p.stem)
    if data.get("policy"):
        data["questions"] = dict(data["questions"])
        data["questions"]["_policy"] = data["policy"]
    return data
