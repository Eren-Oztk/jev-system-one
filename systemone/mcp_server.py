"""MCP sunucusu (stdio): Hermes bu araçları doğal tool olarak çağırır.

Kayıt:  hermes mcp add system-one --command "D:\\system-one\\.venv\\Scripts\\python.exe" \
                                 --args "-m" "systemone.mcp_server"
"""

from __future__ import annotations

import json
from typing import Any

from .client import SystemOneError, ask, has_jev, resolve_engine
from .packs import list_packs, load_pack

try:  # pragma: no cover
    from mcp.server.fastmcp import FastMCP as _Server  # mcp 1.x
except ImportError:  # pragma: no cover
    try:
        from mcp.server.mcpserver import MCPServer as _Server  # mcp 2.x
    except ImportError as e:  # pragma: no cover
        raise SystemExit(
            "mcp paketi yok. Kur: .venv\\Scripts\\python.exe -m pip install mcp"
        ) from e

mcp = _Server("system-one")


@mcp.tool()
def system_one(
    state: Any,
    questions: dict[str, Any] | None = None,
    pack: str | None = None,
    engine: str = "auto",
    model: str | None = None,
) -> dict[str, Any]:
    """Yapılandırılmış karar al: state + tipli sorular (noul/choice/score) -> olasılıklar, confidence, verdict.

    Tek çağrıda TÜM soruları sor; paralel değerlendirilir, süre neredeyse değişmez.
    Sorular atomik olmalı: tek bir yargı. Aritmetik/tarih/sayım işini koda bırak.

    Args:
        state: Değerlendirilecek içerik (metin, JSON nesnesi ya da dizi).
        questions: {"id": {"type":"noul|choice|score", "instructions": "...", "criteria": ...}}
        pack: Hazır karar seti adı (questions yerine): support_triage, lead_qualify,
              llm_guardrail, hermes_route.
        engine: auto | jev | adapter (adapter = LLM fallback, kalibre değil).
        model: Motor üzerinde model adı (örn. jev-latest).
    """
    if pack:
        data = load_pack(pack)
        qs = dict(data["questions"])
        qs.setdefault("_policy", data.get("policy") or {})
    else:
        qs = dict(questions or {})
    if not qs:
        raise SystemOneError("questions ya da pack vermelisin.")
    result = ask(state, qs, model=model, engine=engine)
    return result.to_dict(state=state)


@mcp.tool()
def jev_packs() -> dict[str, Any]:
    """Kullanılabilir karar pack'lerini ve sorularını listele."""
    out: dict[str, Any] = {}
    for name in sorted(list_packs()):
        try:
            data = load_pack(name)
            out[name] = {
                "description": data.get("description", ""),
                "questions": {
                    qid: {"type": s.get("type"), "instructions": s.get("instructions")}
                    for qid, s in data["questions"].items()
                    if not qid.startswith("_")
                },
            }
        except Exception as e:  # pragma: no cover
            out[name] = {"error": str(e)}
    return out


@mcp.tool()
def jev_doctor(live: bool = False) -> dict[str, Any]:
    """Motor durumu: Jev anahtarı var mı, fallback ne, canlı çağrı gecikmesi."""
    info: dict[str, Any] = {"has_typesafe_key": has_jev(), "packs": sorted(list_packs())}
    try:
        eng = resolve_engine("auto")
        info.update({"engine": eng.name, "detail": eng.detail, "calibrated": eng.calibrated})
        if eng.note:
            info["warning"] = eng.note
    except SystemOneError as e:
        info["error"] = str(e)
        return info
    if live:
        r = ask(
            "Siparişim iki gün gecikti, iade etmek istiyorum.",
            {"is_urgent": {"type": "noul", "instructions": "Mesaj aciliyet ifade ediyor mu?"}},
        )
        info["live"] = {"latency_ms": r.latency_ms, "model": r.model, "noul": r.answers["is_urgent"].value}
    return info


def main() -> int:
    mcp.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
