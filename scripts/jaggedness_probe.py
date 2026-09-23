#!/usr/bin/env python
"""Dokümante edilmiş zayıflıkları CANLI doğrular (jev-1.13 jaggedness).

Amaç: "skill ne öğretiyorsa doğru mu?" sorusunu ölçmek. Her prob için
beklenen davranış TypeSafe'in kendi dokümanından alındı; burada canlı
çağrıyla karşılaştırılıyor ve doğru cevap KOD ile hesaplanıyor.

Kullanım: python scripts/jaggedness_probe.py [--engine auto|jev|adapter]
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from systemone import ask  # noqa: E402

RESULTS: list[tuple[str, str, str, str]] = []


def record(probe: str, expected: str, actual: str, verdict: str) -> None:
    RESULTS.append((probe, expected, actual, verdict))
    print(f"\n[{probe}]")
    print(f"  doküman diyor : {expected}")
    print(f"  canlı sonuç   : {actual}")
    print(f"  hüküm         : {verdict}")


def probe_count(engine: str) -> None:
    items = ["typesafe", "apple", "california", "banana", "likes", "calibration", "orange", "vertex",
             "pineapple", "melon", "cherry", "grape", "keyboard", "chimera", "plum", "fig",
             "monitor", "server", "peach", "apricot", "network", "router", "kernel", "mango"]
    fruits = {"apple", "banana", "orange", "pineapple", "melon", "cherry", "grape", "plum", "fig",
              "peach", "apricot", "mango"}
    truth = sum(1 for i in items if i in fruits)
    r = ask(
        {"items": items},
        {
            "count_fruits": {
                "type": "choice",
                "instructions": "`items` listesindeki meyve adlarının TAM SAYISI kaçtır?",
                "criteria": {str(n): f"{n} meyve" for n in range(len(items) + 1)},
            },
        },
        engine=engine,
    )
    got = int(r.answers["count_fruits"].value)
    ok = got == truth
    record(
        f"sayım / {len(items)} öğe (tek soruda)",
        "güvenilmez; hatayı kodla hesapla (jev-1.13 jaggedness #2)",
        f"model {got} dedi, kod {truth} hesapladı (conf {r.answers['count_fruits'].confidence})",
        "✓ doküman doğru" if not ok else "✗ doküman bu vakada tutmadı (model bildi)",
    )


def probe_char_count(engine: str) -> None:
    word = "defenselessness"
    truth = word.count("e")
    r = ask(
        f"Kelime: '{word}'.",
        {
            "e_count": {
                "type": "choice",
                "instructions": f"'{word}' kelimesinde kaç adet 'e' harfi var?",
                "criteria": {str(n): f"{n} adet 'e'" for n in range(0, 9)},
            },
        },
        engine=engine,
    )
    got = int(r.answers["e_count"].value)
    ok = got == truth
    record(
        "karakter sayımı",
        "model cevabın şeklini tanır, saymaz; hata boyutla büyür (#2)",
        f"model {got} dedi, kod {truth} hesapladı (conf {r.answers['e_count'].confidence})",
        "✓ doküman doğru" if not ok else "✗ doküman bu vakada tutmadı (model bildi)",
    )


def probe_date_window(engine: str) -> None:
    signed, invoiced, window = date(2025, 2, 15), date(2025, 3, 3), 30
    truth = invoiced <= signed + timedelta(days=window)
    state = (
        "Sözleşme tutanağı: 'Sözleşme 15.02.2025 tarihinde imzalanmıştır.' "
        "Muhasebe kaydı: 'Fatura 03.03.2025 tarihinde düzenlenmiştir.' "
        "Politika: 'Fatura, sözleşme imza tarihinden itibaren 30 gün içinde düzenlenmiş olmalıdır.'"
    )
    r = ask(
        state,
        {
            "within_window": {
                "type": "noul",
                "instructions": "Fatura, imza tarihinden itibaren 30 günlük süre içinde düzenlenmiş mi?",
            },
        },
        engine=engine,
    )
    v = float(r.answers["within_window"].value)
    model_says = v >= 0.8 if v not in (0.0,) else False
    ok = (v >= 0.8) == truth
    record(
        "tarih aralığı",
        "tarihleri sıralı nicelik gibi okumaz; çıkarımı modele, hesabı koda ver (#3)",
        f"model noul={v:.2f} (yani '{'evet' if v>=0.8 else 'hayır/belirsiz'}'), kod 'evet' ({signed}+30g >= {invoiced})",
        "✓ doküman doğru (güvenilmez)" if not ok else "~ bu vakada doğru bildi (yine de koda bırak)",
    )


def probe_math(engine: str) -> None:
    net, kdv = 349.0, 0.18
    truth = round(net * (1 + kdv), 2)
    state = "Ürün fiyatı 349 TL (KDV hariç). KDV oranı %18."
    r = ask(
        state,
        {
            "total": {
                "type": "choice",
                "instructions": "KDV dahil toplam tutar hangisi?",
                "criteria": {"411.82": "KDV dahil 411,82 TL", "409.50": "KDV dahil 409,50 TL",
                             "380.41": "KDV dahil 380,41 TL", "425.00": "KDV dahil 425,00 TL"},
            },
        },
        engine=engine,
    )
    got = r.answers["total"].value
    ok = got == "411.82"
    record(
        "aritmetik",
        "Jev hesap makinesi değil; aritmetiği kodda yap (#2)",
        f"model '{got}' (conf {r.answers['total'].confidence}), kod {truth}",
        "~ doğruyu buldu (yine de koda bırak)" if ok else "✓ doküman doğru (yanlış hesap)",
    )


def probe_invariant(engine: str) -> None:
    state = "Siparişim iki kez ücretlendirildi. Bir bakabilir misiniz?"
    r = ask(
        state,
        {
            "refund_noul": {"type": "noul", "instructions": "Müşteri iade talep ediyor mu?"},
            "refund_choice": {
                "type": "choice",
                "instructions": "Müşteri iade talep ediyor mu?",
                "criteria": {"yes": "Evet, iade talep ediyor", "no": "Hayır, iade talebi yok"},
            },
        },
        engine=engine,
    )
    noul = float(r.answers["refund_noul"].value)
    ch = r.answers["refund_choice"]
    p_yes = ch.probabilities.get("yes", 0.0)
    fark = abs(noul - p_yes)
    record(
        "yapısal değişmez (noul vs choice)",
        "aynı sorunun noul'u ile choice yes olasılığı eşit olmak zorunda değil (#8)",
        f"noul={noul:.2f}  choice_yes={p_yes:.2f}  fark={fark:.2f}",
        "✓ doküman doğru" if fark > 0.05 else "~ bu vakada örtüştüler",
    )


def probe_literal(engine: str) -> None:
    state = "Para iadesi istemiyorum, sadece ürünü büyük bedenle değiştirmek istiyorum."
    r = ask(
        state,
        {"wants_refund": {"type": "noul", "instructions": "Müşteri para iadesi talep ediyor mu?"}},
        engine=engine,
    )
    v = float(r.answers["wants_refund"].value)
    ok = v <= 0.2
    record(
        "literal okuma",
        "soruyu yazdığın gibi okur (kastetmek istediğini değil) (#1)",
        f"noul={v:.2f} (beklenen: ≤0.20 — müşteri açıkça istemiyorum diyor)",
        "✓ doküman doğru (literal davrandı)" if ok else "! literal olması beklenirdi, karıştırdı",
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", default="auto", choices=["auto", "jev", "adapter"])
    args = ap.parse_args()

    for fn in (probe_count, probe_char_count, probe_date_window, probe_math, probe_invariant, probe_literal):
        try:
            fn(args.engine)
        except Exception as e:  # noqa: BLE001
            record(fn.__name__, "-", f"HATA: {e}", "! ölçülemedi")

    print("\n" + "=" * 72)
    print("ÖZET: doküman iddiaları canlı doğrulandı mı?")
    for probe, _, _, verdict in RESULTS:
        print(f"  {verdict:<45} {probe}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
