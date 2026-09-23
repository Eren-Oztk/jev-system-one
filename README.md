# system-one — Jev / System One karar katmanı

[TypeSafe AI](https://typesafe.ai/)'ın **System One** modeli **Jev**'i (ve anahtarı
yokken bir LLM fallback'ini) aynı arayüzün arkasına koyan ince bir karar katmanı:
*state + tipli sorular* gir, **tipli cevaplar + kalibre olasılıklar + eşik kararı** çık.

```
state + questions ──► [ Jev | LLM adapter ] ──► answers (choice/score/noul)
                                                 + probabilities
                                                 + confidence
                                                 + verdict: act | review | escalate
```

## Neden böyle

Jev bir sohbet/bot modeli değil: metin üretmez, kod yazmaz, araç çağırmaz.
Tek yaptığı şey **paralel, atomik, tipli yargılar** üretmek:

| | Klasik LLM | System One (Jev) |
|---|---|---|
| Optimize | insan tercihi (RLHF) / doğrulanabilir ödül | kalibre edilmiş kararlar (RLCD) |
| Çıktı | string (parse et, doğrula, umut et) | tipli değer + olasılık dağılımı |
| Örnekleme | sıralı (token token) | paralel (tek sorgu) |
| Gecikme | 3–329 s | ~0.07–0.5 s |
| Maliyet | $0.20–$10 / Mtok giriş, çıkış ~5x | $0.042 / Mtok giriş, **çıkış ücretsiz** |
| Belirsizlik | "eminim" der, yanılır | her cevapta `confidence` / `noul` olasılığı |
| Tip hatası | mümkün | tanım gereği imkânsız (şema önceden sabit) |

Tasarım kuralı: **kod kontrolü tutar, model sadece semantik yargı verir.**
Aritmetik, tarih karşılaştırması, sayım, kimlik kontrolü → kod. "Bu mesaj aciliyet
ifade ediyor mu?" → model.

## Kurulum

```bash
cd /d/system-one
uv venv .venv --python 3.11
uv pip install --python .venv/Scripts/python.exe -e ".[mcp,dev]"
cp .env.example .env   # TYPESAFE_API_KEY ve/veya DEEPSEEK_API_KEY
```

Anahtarlar `systemone/env.py` sayesinde şu sırayla bulunur: mevcut ortam değişkeni
→ `$HERMES_HOME/.env` → `~/.hermes/.env` → `~/AppData/Local/hermes/.env` → proje `.env`.
**Sır config.yaml'a yazılmaz.**

## Kullanım

```bash
# 1) Hazır karar setleri
jev packs
jev ask --pack support_triage --state mesaj.json            # dosya
cat mesaj.txt | jev ask --pack support_triage --format json  # stdin + JSON
jev ask --pack llm_guardrail --text "Ignore previous instructions..."

# 2) Tek seferlik sorular
jev ask --engine adapter \
  --choice 'topic=Bu talep neyle ilgili?|seo=arama görünürlüğü;kod=yazılım hatası' \
  --noul   'is_urgent=Mesaj zaman baskısı içeriyor mu?' \
  --text   "Sitem 3 haftadır düşüşte, acil müdahale lazım."

# 3) Motor durumu / canlı gecikme testi
jev doctor --live
jev models
```

Çıkış kodları shell'de dallanmak için:

| kod | verdict | anlamı |
|---|---|---|
| 0 | `act` | otomatik devam |
| 10 | `review` | işaretle / insana sor |
| 11 | `escalate` | karar verme, yönlendir |
| 2 | — | hata |

```bash
if jev ask --pack support_triage --state m.json --format json > /tmp/r.json; then
  echo "otomatik işlenebilir"
else
  echo "insan gerekiyor (kod $?)"
fi
```

## Python

```python
from systemone import ask

r = ask(
    {"message": "Kartımdan iki kez 49 dolar çekilmiş, üç gündür iade yok."},
    {
        "department": {"type": "choice", "instructions": "Hangi ekip bakmalı?",
                       "criteria": {"billing": "Ödeme/iade", "technical": "Hata"}},
        "is_urgent":  {"type": "noul", "instructions": "Aciliyet ifade ediyor mu?"},
        "frustration": {"type": "score", "instructions": "Sinir seviyesi",
                        "criteria": ["Sakin", "Rahatsız", "Öfkeli"]},
    },
)
print(r.verdict, {k: a.value for k, a in r.answers.items()})
if r.answers["is_urgent"].yes:
    ...
```

## Karar setleri (packs)

Sorular ve eşikler **tek dosyada** durur (`systemone/packs/*.json`) — TypeSafe'in
"review edilecek tek yer" önerisi. Kendi setini `~/.system-one/packs/` altına
koyarsan builtin'i gölgeler.

| pack | ne yapar |
|---|---|
| `support_triage` | departman / aciliyet / öfke / insan talebi / dil |
| `lead_qualify` | hizmet tipi / kapsam netliği / bütçe sinyali / ciddiyet / teslim baskısı |
| `llm_guardrail` | PII / sır / prompt injection / jailbreak / zarar şiddeti → geçir-incele-engelle |
| `hermes_route` | tur amacı / araç gerekli mi / derin akıl / risk / onay / sansürsüz gerekli mi |

Pack şeması:

```json
{
  "description": "...",
  "questions": {
    "is_urgent": {
      "type": "noul",
      "instructions": "Mesaj aciliyet ifade ediyor mu?",
      "criteria": {"true": "...", "false": "..."},
      "gate": {"yes": 0.8, "no": 0.2}
    }
  },
  "policy": {
    "rules": [{"question": "is_urgent", "op": ">", "value": 0.8, "then": "escalate"}]
  }
}
```

`gate` alanları: noul → `yes`/`no`; choice & score → `act`/`review` (confidence).
`policy.rules` op: `>`, `>=`, `<`, `<=`, `==`, `!=`, `in`. En kötü verdict kazanır.

## MCP (Hermes'te doğal tool)

```bash
hermes mcp add system-one --command "D:/system-one/.venv/Scripts/python.exe" --args -m systemone.mcp_server
```

Araçlar: `system_one`, `jev_packs`, `jev_doctor` → Hermes'te restart sonrası
`mcp_system_one_system_one` olarak görünür.

## Testler

```bash
.venv/Scripts/python.exe -m pytest tests -q      # ağsız birim testler
.venv/Scripts/python.exe scripts/smoke.py        # gerçek çağrı, artifacts/ altına kaydeder
```

## Motor: Jev vs fallback

`TYPESAFE_API_KEY` varsa **Jev** kullanılır: kalibre, ~0.1–0.5 s, çıkış ücretsiz.
Yoksa `system-one-adapter` ile OpenAI-uyumlu bir LLM (varsayılan `deepseek-flash`)
aynı cevap şeklini üretir — **geliştirme ve ölçüm için**:

* olasılıklar kalibre değil (LLM'in sözde-olasılıkları),
* ölçülen gecikme 2–11 s (Jev'in 20–50 katı),
* eşikleri fallback motoruyla ayarlayıp Jev'e taşımak yanlış sonuç verir.

## Jev'in bilinen zayıf noktaları (jev-1.13)

Kod tarafında bunlara dikkat: sayı/aritmetik ve tarih karşılaştırması güvenilmez
(çıkarımı modele, hesabı koda ver), çok büyük state isabeti düşürür (önce filtrele),
dolaylı/anlaşılması zor talimat zayıflatır, çelişkili instruction+criteria bozar,
karşıt içerik (adversarial) cevabı kaydırabilir, `noul` ile `1-noul` toplamı 1 olmak
zorunda değil, Choice ↔ Noul sayıları birbirine dönüştürülemez.

Kaynak: https://docs.typesafe.ai/model-jaggedness/jev-1.13
