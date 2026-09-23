"""jev — komut satırından System One kararı.

Çıkış kodları (shell'de dallanmak için):
  0  act      -> otomatik devam
  10 review   -> işaretle / insana sor
  11 escalate -> karar verme, insana yönlendir
  2  hata
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .client import (
    FALLBACK_BASE_URL,
    FALLBACK_MODEL,
    JEV_DEFAULT_MODEL,
    SystemOneError,
    ask,
    has_jev,
    resolve_engine,
)
from .packs import list_packs, load_pack

EXIT = {"act": 0, "review": 10, "escalate": 11}


def _out(text: str = "") -> None:
    sys.stdout.write(text + "\n")


def _err(text: str = "") -> None:
    sys.stderr.write(text + "\n")


def _stdout_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except Exception:
            pass


def _read_state(args: argparse.Namespace) -> Any:
    if args.text is not None:
        return args.text
    if args.state in (None, "-"):
        raw = sys.stdin.read()
    else:
        raw = Path(args.state).read_text(encoding="utf-8")
    raw = raw.strip()
    if not raw:
        raise SystemOneError("state boş. --text ver ya da dosya/stdin gönder.")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def _parse_inline(args: argparse.Namespace) -> dict[str, dict[str, Any]]:
    """--noul/--score/--choice ile hızlı soru tanımı."""
    qs: dict[str, dict[str, Any]] = {}
    for spec in args.noul or []:
        qid, _, instr = spec.partition("=")
        qs[qid.strip()] = {"type": "noul", "instructions": instr.strip()}
    for spec in args.score or []:
        qid, _, rest = spec.partition("=")
        instr, _, levels = rest.partition("|")
        qs[qid.strip()] = {
            "type": "score",
            "instructions": instr.strip(),
            "criteria": [l.strip() for l in levels.split(";") if l.strip()],
        }
    for spec in args.choice or []:
        qid, _, rest = spec.partition("=")
        instr, _, opts = rest.partition("|")
        criteria: dict[str, Any] = {}
        for pair in opts.split(";"):
            if not pair.strip():
                continue
            if "=" in pair:
                k, _, v = pair.partition("=")
            else:
                k, _, v = pair.partition(":")
            criteria[k.strip()] = v.strip() or None
        qs[qid.strip()] = {
            "type": "choice",
            "instructions": instr.strip(),
            "criteria": criteria,
        }
    return qs


def _emit(result: Any, fmt: str, state: Any) -> int:
    if fmt == "json":
        _out(json.dumps(result.to_dict(state=state), ensure_ascii=False, indent=2))
    else:
        _err(f"motor : {result.engine} ({result.model})"
             + ("" if result.calibrated else "  [KALİBRE DEĞİL]"))
        _err(f"süre  : {result.latency_ms} ms | token in={result.input_tokens} out={result.output_tokens}"
             + (f" | ${result.cost_usd:.6f}" if result.cost_usd is not None else ""))
        for a in result.answers.values():
            conf = f" conf={a.confidence:.2f}" if a.confidence is not None else ""
            mark = {"act": "✓", "review": "?", "escalate": "!"}[a.verdict]
            _out(f"  {mark} {a.id:<18} {a.type:<6} {a.value!r}{conf}  [{a.verdict}]")
            if a.probabilities and a.type != "noul":
                top = sorted(a.probabilities.items(), key=lambda kv: -kv[1])[:3]
                _err("      " + ", ".join(f"{k}:{v:.2f}" for k, v in top))
        _err(f"KARAR : {result.verdict.upper()}")
        for n in result.notes:
            _err(f"not   : {n}")
    return EXIT[result.verdict]


def cmd_ask(args: argparse.Namespace) -> int:
    if args.pack:
        pack = load_pack(args.pack)
        questions = dict(pack["questions"])
        questions.setdefault("_policy", pack.get("policy") or {})
    else:
        questions = _parse_inline(args)
        if not questions:
            raise SystemOneError("Soru yok: --pack ver ya da --noul/--score/--choice kullan.")
    state = _read_state(args)
    result = ask(
        state,
        questions,
        model=args.model,
        engine=args.engine,
        timeout=args.timeout,
    )
    return _emit(result, args.format, state)


def cmd_doctor(args: argparse.Namespace) -> int:
    _err(f"systemone v{__version__}")
    _err(f"python  : {sys.version.split()[0]}")
    _err(f"TYPESAFE_API_KEY : {'VAR (Jev kullanılabilir)' if has_jev() else 'yok'}")
    _err(f"DEEPSEEK_API_KEY : {'var' if os.environ.get('DEEPSEEK_API_KEY') else 'yok'}")
    try:
        eng = resolve_engine("auto")
        _err(f"seçili motor     : {eng.name} -> {eng.detail}")
        if eng.note:
            _err(f"uyarı            : {eng.note}")
    except SystemOneError as e:
        _err(f"HATA: {e}")
        return 2
    packs = list_packs()
    _err(f"pack'ler ({len(packs)}): {', '.join(sorted(packs))}")
    if args.live:
        state = "Kartımdan iki kez 49 dolar çekilmiş, üç gündür iade alamıyorum, bu beni çok mağdur etti."
        qs = {
            "is_urgent": {"type": "noul", "instructions": "Mesaj aciliyet ifade ediyor mu?"},
            "department": {
                "type": "choice",
                "instructions": "Hangi ekip bakmalı?",
                "criteria": {"billing": "Ödeme/iade", "technical": "Hata", "sales": "Fiyat"},
            },
        }
        r = ask(state, qs, engine=args.engine)
        _err(f"canlı test       : {r.latency_ms} ms, {r.model}")
        for a in r.answers.values():
            _err(f"  {a.id}: {a.value!r} conf={a.confidence}")
    return 0


def cmd_packs(args: argparse.Namespace) -> int:
    packs = list_packs()
    if args.show:
        data = load_pack(args.show)
        _out(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    for name, path in sorted(packs.items()):
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            desc = data.get("description", "")
            n = len(data.get("questions", {}))
        except Exception:
            desc, n = "(okunamadı)", 0
        _out(f"{name:<18} {n} soru  {desc}")
    return 0


def cmd_models(args: argparse.Namespace) -> int:
    eng = resolve_engine(args.engine)
    _err(f"motor: {eng.name} -> {eng.detail}")
    if eng.name == "jev":
        from typesafe_sdk import TypeSafeClient

        with TypeSafeClient() as client:
            for m in client.models.list().models:
                _out(f"{m.name:<16} {m.release_date}  {m.description}")
    else:
        import urllib.request

        req = urllib.request.Request(
            f"{FALLBACK_BASE_URL.rstrip('/')}/models",
            headers={"Authorization": f"Bearer {os.environ.get('DEEPSEEK_API_KEY', '')}"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            data = json.load(resp)
        for m in data.get("data", []):
            _out(m.get("id", "?"))
        _err(f"(adapter varsayılanı: {FALLBACK_MODEL})")
    return 0


def cmd_mcp(args: argparse.Namespace) -> int:
    from .mcp_server import main as mcp_main

    return mcp_main()


def cmd_hook(args: argparse.Namespace) -> int:
    """Hermes shell-hook köprüsü: stdin'den payload, stdout'a karar."""
    from .hook import main as hook_main

    return hook_main()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="jev", description="System One / Jev karar CLI'ı")
    p.add_argument("--version", action="version", version=f"systemone {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("ask", help="state + sorular -> tipli kararlar")
    a.add_argument("--pack", help="pack adı ya da json yolu")
    a.add_argument("--state", help="state dosyası (yok/- ise stdin)")
    a.add_argument("--text", help="state'i doğrudan metin olarak ver")
    a.add_argument("--noul", action="append", help="id=soru metni")
    a.add_argument("--score", action="append", help="id=soru|seviye1;seviye2;seviye3")
    a.add_argument("--choice", action="append", help="id=soru|opt1=tanım;opt2=tanım")
    a.add_argument("--engine", default="auto", choices=["auto", "jev", "adapter"])
    a.add_argument("--model", default=None)
    a.add_argument("--timeout", type=float, default=None)
    a.add_argument("--format", default="text", choices=["text", "json"])
    a.set_defaults(func=cmd_ask)

    d = sub.add_parser("doctor", help="ortam/motor kontrolü")
    d.add_argument("--live", action="store_true", help="gerçek bir çağrı da yap")
    d.add_argument("--engine", default="auto", choices=["auto", "jev", "adapter"])
    d.set_defaults(func=cmd_doctor)

    k = sub.add_parser("packs", help="pack'leri listele/göster")
    k.add_argument("--show", help="pack içeriğini yazdır")
    k.set_defaults(func=cmd_packs)

    m = sub.add_parser("models", help="erişilebilir modeller")
    m.add_argument("--engine", default="auto", choices=["auto", "jev", "adapter"])
    m.set_defaults(func=cmd_models)

    sub.add_parser("mcp", help="MCP stdio sunucusunu çalıştır").set_defaults(func=cmd_mcp)
    sub.add_parser(
        "hook", help="Hermes shell-hook köprüsü (pre_tool_call guardrail, stdin→stdout)"
    ).set_defaults(func=cmd_hook)
    return p


def main(argv: list[str] | None = None) -> int:
    _stdout_utf8()
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except SystemOneError as e:
        _err(f"HATA: {e}")
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
