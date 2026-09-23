#!/usr/bin/env python
"""Etiketli mini eval: doğruluk + kalibrasyon + gecikme/maliyet.

Kullanım:
    python scripts/eval.py                     # otomatik motor
    python scripts/eval.py --engine adapter    # DeepSeek fallback
    python scripts/eval.py --engine jev        # Jev (anahtar gerekir)

Ne ölçer:
  * her soru için doğruluk (choice: tam eşleşme, noul: gate eşiğiyle evet/hayır)
  * KALİBRASYON: confidence kovalarında gerçek doğruluk (kalibre ise artmalı)
  * gecikme (ortalama/p50), token, tahmini maliyet

Not: 12 vaka küçük bir settir; gösterge niteliğindedir, garanti değil.
Etiketler bu deponun yazarı tarafından konuldu (insan yargısı).
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from systemone import ask  # noqa: E402
from systemone.packs import load_pack  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "evals" / "support_cases.jsonl"
CHECKED = ("department", "is_urgent", "wants_human")
BUCKETS = [(0.0, 0.6), (0.6, 0.8), (0.8, 0.95), (0.95, 1.0001)]


def load_cases() -> list[dict]:
    return [json.loads(l) for l in CASES.read_text(encoding="utf-8").splitlines() if l.strip()]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", default="auto", choices=["auto", "jev", "adapter"])
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    cases = load_cases()
    if args.limit:
        cases = cases[: args.limit]
    pack = load_pack("support_triage")
    questions = {k: v for k, v in pack["questions"].items() if k in CHECKED}
    questions["_policy"] = pack.get("policy") or {}

    rows: list[dict] = []
    latencies: list[int] = []
    tokens_in = tokens_out = 0
    cost_total = 0.0
    engine_used = model_used = None

    for c in cases:
        t0 = time.perf_counter()
        r = ask(c["state"], questions, engine=args.engine)
        el = int((time.perf_counter() - t0) * 1000)
        engine_used, model_used = r.engine, r.model
        latencies.append(r.latency_ms)
        tokens_in += r.input_tokens or 0
        tokens_out += r.output_tokens or 0
        cost_total += r.cost_usd or 0.0

        row = {"id": c["id"], "latency_ms": r.latency_ms, "correct": {}, "got": {}, "conf": {}}
        for qid in CHECKED:
            a = r.answers[qid]
            exp = c["expect"][qid]
            got = a.value
            if qid == "department":
                ok = got == exp
                row["conf"][qid] = a.confidence
            else:
                ok = (a.yes is True) == bool(exp)
                row["conf"][qid] = r.answers["department"].confidence  # noul'da confidence yok
            row["correct"][qid] = ok
            row["got"][qid] = got
        rows.append(row)
        mark = "✓" if all(row["correct"].values()) else "✗"
        print(f"{mark} {c['id']}  {el:>6} ms  " +
              "  ".join(f"{q}={row['got'][q]!r}{'' if row['correct'][q] else '(BEKLENEN '+repr(c['expect'][q])+')'}"
                        for q in CHECKED))

    print(f"\nmotor: {engine_used} / {model_used}")
    per_q = {q: sum(r["correct"][q] for r in rows) / len(rows) for q in CHECKED}
    all_ok = sum(all(r["correct"].values()) for r in rows) / len(rows)
    print(f"vaka sayısı: {len(rows)}")
    for q, acc in per_q.items():
        print(f"  {q:<12} doğruluk: {acc:.1%}")
    print(f"  TÜM sorular doğru olan vaka: {all_ok:.1%}")

    print("\nkalibrasyon (choice confidence → gerçek doğruluk):")
    for lo, hi in BUCKETS:
        sel = [r for r in rows if r["conf"]["department"] is not None and lo <= r["conf"]["department"] < hi]
        if not sel:
            print(f"  {lo:.2f}-{hi:.2f}: vaka yok")
            continue
        acc = sum(r["correct"]["department"] for r in sel) / len(sel)
        print(f"  {lo:.2f}-{hi:.2f}: {len(sel):>2} vaka, doğruluk {acc:.0%}")

    print(f"\ngecikme: ortalama {statistics.mean(latencies):.0f} ms, p50 {statistics.median(latencies):.0f} ms, "
          f"min {min(latencies)} ms, max {max(latencies)} ms")
    print(f"token: giriş {tokens_in}, çıkış {tokens_out}"
          + (f", tahmini maliyet ${cost_total:.6f}" if cost_total else " (maliyet: fiyat env'i yok)"))

    out = ROOT / "artifacts" / f"eval-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({
        "engine": engine_used, "model": model_used, "n": len(rows),
        "accuracy": per_q, "all_correct": all_ok,
        "latency": {"mean": statistics.mean(latencies), "p50": statistics.median(latencies),
                    "min": min(latencies), "max": max(latencies)},
        "tokens": {"in": tokens_in, "out": tokens_out}, "cost_usd": cost_total or None,
        "rows": rows,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nkayıt: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
