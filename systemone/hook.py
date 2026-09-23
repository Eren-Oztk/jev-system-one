"""Hermes shell-hook köprüsü: pre_tool_call guardrail.

Akış (TypeSafe felsefesi: önce kod filtreler, modele yalnızca gerçek yargı gider):
  1. Hermes stdin'e JSON payload verir (tool_name, tool_input, ...).
  2. Ucuz regex ön filtre: şüpheli desen yoksa HİÇ model çağrılmaz (0 ms, 0 maliyet).
  3. Şüpheliyse System One'a 3 atomik soru sorulur (yıkıcılık, sır sızıntısı,
     dışarıya veri gönderme) + severity skoru.
  4. Yüksek confidence + yüksek risk → blok (exit 2 + stdout JSON), değilse sessiz geçiş.

Hermes sözleşmesi: stdout'a geçerli JSON ya da boş çıktı; exit 2 = blok.
"""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Any

# ---------------------------------------------------------------- ön filtre
DANGER = re.compile(
    r"(rm\s+-rf\s+/|rm\s+-rf\s+~|rm\s+-rf\s+\*|mkfs\.|dd\s+.*of=/dev/|diskpart|"
    r"format\s+[a-z]:|shutdown\s+/[sr]|Remove-Item\s+.*-Recurse.*-Force\s+[A-Z]:\\?$|"
    r"chmod\s+-R\s+777\s+/|:\(\)\s*\{.*\};\s*:|>\s*/dev/sd|DROP\s+DATABASE|"
    r"git\s+push\s+.*--force.*\bmain\b|git\s+reset\s+--hard\s+origin)",
    re.IGNORECASE,
)
SECRET = re.compile(
    r"(sk-[A-Za-z0-9]{16,}|ghp_[A-Za-z0-9]{20,}|github_pat_|AKIA[0-9A-Z]{16}|"
    r"-----BEGIN\s+[A-Z ]*PRIVATE KEY-----|xox[baprs]-[A-Za-z0-9-]{10,}|"
    r"(api[_-]?key|token|password|passwd|secret)\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{12,})",
    re.IGNORECASE,
)
EXFIL = re.compile(
    r"(curl[^\n|]*(--data|-d\s|-T\s|--upload-file|-F\s)|nc\s+-e|base64[^\n|]*\|\s*curl|"
    r"Invoke-WebRequest[^\n]*-Method\s+Post|scp\s+[^\n]*@|rsync\s+[^\n]*@)",
    re.IGNORECASE,
)
LOCAL_OK = re.compile(r"(127\.0\.0\.1|localhost|::1|10\.66\.|192\.168\.|file://)", re.IGNORECASE)

QUESTIONS: dict[str, dict[str, Any]] = {
    "destructive": {
        "type": "noul",
        "instructions": "Bu işlem geri döndürülemez biçimde veri, disk ya da sistemi siliyor/biçimlendiriyor mu?",
        "criteria": {
            "true": "Silme, biçimlendirme, disk yazma, veritabanı düşürme, zorla geçmiş yazma",
            "false": "Salt okuma, geri alınabilir yazma, normal komut",
        },
        "gate": {"yes": 0.85, "no": 0.2},
    },
    "secret_exposure": {
        "type": "noul",
        "instructions": "İşlem gerçek bir sırrı (özel anahtar, API anahtarı, parola, token) açık biçimde komut satırına ya da bir dosyaya yazıyor mu?",
        "criteria": {
            "true": "Sır değeri metnin içinde görünür durumda",
            "false": "Sır yok ya da yalnızca değişken adı geçiyor",
        },
        "gate": {"yes": 0.8, "no": 0.2},
    },
    "exfiltration": {
        "type": "noul",
        "instructions": "İşlem yerel dosya ya da veriyi harici bir adrese gönderiyor mu?",
        "criteria": {
            "true": "Veri dış bir sunucuya/dışarıya aktarılıyor",
            "false": "Yerel, salt okuma ya da dışarıya veri gitmiyor",
        },
        "gate": {"yes": 0.75, "no": 0.2},
    },
}

BLOCK_AT = {"destructive": 0.85, "secret_exposure": 0.8, "exfiltration": 0.75}


def _payload_text(payload: dict[str, Any]) -> str:
    tool = payload.get("tool_name") or ""
    args = payload.get("tool_input") or payload.get("args") or {}
    try:
        body = json.dumps(args, ensure_ascii=False)
    except (TypeError, ValueError):
        body = str(args)
    return f"{tool} {body}".strip()


def prefilter(text: str) -> list[str]:
    """Hangi sinyal sınıfları ön filtreyi geçti? Boş liste => model çağrısı yok."""
    hits: list[str] = []
    if DANGER.search(text):
        hits.append("destructive")
    if SECRET.search(text):
        hits.append("secret_exposure")
    if EXFIL.search(text) and not LOCAL_OK.search(text):
        hits.append("exfiltration")
    return hits


def decide(payload: dict[str, Any], engine: str = "auto") -> tuple[int, dict[str, Any]]:
    """(exit_code, stdout_json) döner. exit 2 = blok."""
    text = _payload_text(payload)
    hits = prefilter(text)
    if not hits:
        return 0, {}  # ucuz yol: model çağrısı yok

    from .client import SystemOneError, ask

    qs = {"destructive": QUESTIONS["destructive"]}
    if "secret_exposure" in hits:
        qs["secret_exposure"] = QUESTIONS["secret_exposure"]
    if "exfiltration" in hits:
        qs["exfiltration"] = QUESTIONS["exfiltration"]

    state = {
        "tool": payload.get("tool_name"),
        "arguments": payload.get("tool_input") or payload.get("args"),
        "cwd": payload.get("cwd"),
        "prefilter_signals": hits,
    }
    try:
        result = ask(state, qs, engine=engine)
    except SystemOneError as e:
        # motor yoksa fail-open (varsayılan) — bloklamak için fail_closed gerekir
        print(f"jev hook: motor hatası: {e}", file=sys.stderr)
        return 0, {}

    for qid, threshold in BLOCK_AT.items():
        a = result.answers.get(qid)
        if a and float(a.value) >= threshold:
            reason = (
                f"System One guardrail ({result.engine}/{result.model}): {qid}={a.value:.2f} "
                f">= {threshold}. Tool: {payload.get('tool_name')}. "
                f"Ön filtre: {', '.join(hits)}. Gerekçe gerekiyorsa manuel onayla."
            )
            return 2, {"decision": "block", "reason": reason}

    return 0, {}


def main(argv: list[str] | None = None) -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        print("jev hook: stdin JSON değil", file=sys.stderr)
        return 0
    engine = os.environ.get("SYSTEMONE_HOOK_ENGINE", "auto")
    code, out = decide(payload, engine=engine)
    if out:
        sys.stdout.write(json.dumps(out, ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
