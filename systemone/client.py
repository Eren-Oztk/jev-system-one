"""Tek arayüz: Jev (TypeSafe) birincil, LLM adapter fallback.

Jev anahtarı varsa (TYPESAFE_API_KEY) doğrudan Jev kullanılır: kalibre edilmiş
olasılıklar, ~70-500ms, $42/Btok giriş, çıkış ücretsiz.
Anahtar yoksa system-one-adapter ile OpenAI-uyumlu bir LLM (varsayılan DeepSeek)
aynı SystemOneResponse şeklini üretir — geliştirme/ölçüm için, üretim için değil:
adapter çıktısı LLM olasılıklarıdır, kalibre değildir (TypeSafe'in kendi notu).
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from typesafe_sdk import Choice, Noul, NoulCriteria, Score

from .env import load_env_quiet

load_env_quiet()

JEV_DEFAULT_MODEL = os.environ.get("TYPESAFE_DEFAULT_MODEL", "jev-latest")
JEV_INPUT_USD_PER_TOKEN = 42.0 / 1_000_000_000  # $42 / milyar giriş token'ı; çıkış ücretsiz

FALLBACK_BASE_URL = os.environ.get("SYSTEMONE_FALLBACK_BASE_URL", "https://api.deepseek.com/v1")
FALLBACK_MODEL = os.environ.get("SYSTEMONE_FALLBACK_MODEL", "deepseek-flash")


class SystemOneError(RuntimeError):
    """İki motor da cevap veremediğinde ya da istek doğrulanamadığında."""


# --------------------------------------------------------------------------- #
# Motor seçimi
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Engine:
    name: str  # "jev" | "adapter"
    detail: str
    calibrated: bool
    note: str = ""


def has_jev() -> bool:
    return bool(os.environ.get("TYPESAFE_API_KEY", "").strip())


def resolve_engine(prefer: str = "auto") -> Engine:
    """prefer: auto | jev | adapter."""
    prefer = (prefer or "auto").lower()
    if prefer == "jev" and not has_jev():
        raise SystemOneError(
            "TYPESAFE_API_KEY yok. console.typesafe.ai/keys adresinden anahtar alıp "
            "TYPESAFE_API_KEY olarak ver (Hermes: hermes-vault add)."
        )
    if prefer in ("auto", "jev") and has_jev():
        return Engine(
            name="jev",
            detail=f"TypeSafe Jev ({JEV_DEFAULT_MODEL})",
            calibrated=True,
        )
    if prefer == "adapter":
        return _adapter_engine()
    if not os.environ.get("DEEPSEEK_API_KEY", "").strip():
        raise SystemOneError(
            "Ne TYPESAFE_API_KEY ne DEEPSEEK_API_KEY var. En az birini tanımla."
        )
    return _adapter_engine()


def _adapter_engine() -> Engine:
    return Engine(
        name="adapter",
        detail=f"{FALLBACK_MODEL} @ {FALLBACK_BASE_URL} (system-one-adapter)",
        calibrated=False,
        note=(
            "LLM arkasında çalışıyor: olasılıklar kalibre değil, gecikme ve maliyet "
            "Jev'den yüksek. Eşikleri bu motorla ayarlayıp Jev'e taşımak YANLIŞ sonuç verir."
        ),
    )


# --------------------------------------------------------------------------- #
# Soru inşası (JSON spec -> SDK nesnesi)
# --------------------------------------------------------------------------- #
def build_question(spec: Mapping[str, Any]) -> Any:
    """Bir pack/JSON soru tanımını SDK nesnesine çevirir."""
    qtype = str(spec.get("type", "")).lower()
    if "instructions" not in spec:
        raise SystemOneError(f"Soru tanımında 'instructions' yok: {spec!r}")
    instructions = spec["instructions"]
    criteria = spec.get("criteria")

    if qtype == "noul":
        kwargs: dict[str, Any] = {"instructions": instructions}
        if criteria:
            if isinstance(criteria, Mapping):
                kwargs["criteria"] = NoulCriteria(**criteria)
            else:
                kwargs["criteria"] = criteria
        return Noul(**kwargs)
    if qtype == "choice":
        if not isinstance(criteria, Mapping) or not criteria:
            raise SystemOneError("choice sorusu için 'criteria' haritası gerekli.")
        return Choice(instructions=instructions, criteria=dict(criteria))
    if qtype == "score":
        if not isinstance(criteria, (list, tuple)) or len(criteria) < 2:
            raise SystemOneError("score sorusu için en az 2 seviyeli 'criteria' listesi gerekli.")
        if len(criteria) > 10:
            raise SystemOneError("score en fazla 10 seviye kabul eder.")
        return Score(instructions=instructions, criteria=list(criteria))
    raise SystemOneError(f"Bilinmeyen soru tipi: {qtype!r} (noul|choice|score)")


def build_questions(specs: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Tüm soruları tek seferde kur — hepsi aynı istekte paralel gider.

    Alt çizgiyle başlayan anahtarlar (_policy) soru değil, yerel politika
    tanımıdır; modele gönderilmez.
    """
    items = {k: v for k, v in specs.items() if not k.startswith("_")}
    if not items:
        raise SystemOneError("En az bir soru gerekli.")
    out: dict[str, Any] = {}
    for qid, spec in items.items():
        clean = {k: v for k, v in spec.items() if k not in ("gate", "note", "description")}
        out[qid] = build_question(clean)
    return out


# --------------------------------------------------------------------------- #
# Sonuç modelleri
# --------------------------------------------------------------------------- #
@dataclass
class Answer:
    id: str
    type: str
    value: Any
    confidence: float | None = None
    probabilities: dict[str, float] = field(default_factory=dict)
    legend: dict[str, Any] | None = None
    verdict: str = "act"  # act | review | escalate
    yes: bool | None = None  # noul için: True/False; belirsizse None (review bandı)

    @property
    def levels(self) -> int | None:
        return len(self.legend) if self.legend else None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "type": self.type,
            "value": self.value,
            "verdict": self.verdict,
        }
        if self.yes is not None:
            d["yes"] = self.yes
        if self.confidence is not None:
            d["confidence"] = round(self.confidence, 4)
        if self.probabilities:
            d["probabilities"] = {k: round(float(v), 4) for k, v in self.probabilities.items()}
        if self.legend:
            d["legend"] = self.legend
        return d


@dataclass
class Result:
    engine: str
    model: str
    answers: dict[str, Answer]
    verdict: str
    latency_ms: int
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    calibrated: bool = True
    notes: list[str] = field(default_factory=list)

    def to_dict(self, state: Any | None = None) -> dict[str, Any]:
        d: dict[str, Any] = {
            "engine": self.engine,
            "model": self.model,
            "verdict": self.verdict,
            "calibrated": self.calibrated,
            "answers": {k: v.to_dict() for k, v in self.answers.items()},
            "latency_ms": self.latency_ms,
            "usage": {
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
                "cost_usd": None if self.cost_usd is None else round(self.cost_usd, 8),
            },
        }
        if self.notes:
            d["notes"] = self.notes
        if state is not None:
            d["state"] = state
        return d


# --------------------------------------------------------------------------- #
# Çağrı
# --------------------------------------------------------------------------- #
def ask(
    state: Any,
    questions: Mapping[str, Mapping[str, Any]],
    *,
    model: str | None = None,
    engine: str = "auto",
    timeout: float | None = None,
) -> Result:
    """Tek istek: state + tüm sorular -> tipli cevaplar + kalibre olasılıklar."""
    if isinstance(state, str) and not state.strip():
        raise SystemOneError("state boş.")
    eng = resolve_engine(engine)
    qobjs = build_questions(questions)

    started = time.perf_counter()
    if eng.name == "jev":
        raw, model_used = _call_jev(state, qobjs, model=model, timeout=timeout)
    else:
        raw, model_used = _call_adapter(state, qobjs, model=model)
    latency_ms = int((time.perf_counter() - started) * 1000)

    answers: dict[str, Answer] = {}
    for qid, spec in questions.items():
        if qid.startswith("_"):
            continue
        ans = getattr(raw, "answers", {}).get(qid)
        if ans is None:
            raise SystemOneError(f"Cevapta '{qid}' yok. Ham cevap: {raw!r}")
        answers[qid] = _to_answer(qid, ans, spec)

    usage = getattr(raw, "usage", None)
    in_tok = getattr(usage, "input_tokens", None) if usage else None
    out_tok = getattr(usage, "output_tokens", None) if usage else None

    cost: float | None = None
    notes: list[str] = []
    if eng.name == "jev" and in_tok:
        cost = in_tok * JEV_INPUT_USD_PER_TOKEN
        if out_tok is None:
            notes.append("output_tokens yok")
    elif eng.name == "adapter":
        price_in = os.environ.get("SYSTEMONE_FALLBACK_PRICE_IN_USD_PER_MTOK")
        price_out = os.environ.get("SYSTEMONE_FALLBACK_PRICE_OUT_USD_PER_MTOK")
        if price_in and in_tok is not None:
            cost = in_tok * float(price_in) / 1e6 + (out_tok or 0) * float(price_out or 0) / 1e6
        else:
            notes.append("fallback maliyeti hesaplanmadı (fiyat env'i tanımlı değil)")
        if eng.note:
            notes.append(eng.note)

    from .policy import overall_verdict

    return Result(
        engine=eng.name,
        model=model_used or getattr(raw, "model", "unknown"),
        answers=answers,
        verdict=overall_verdict(answers, questions),
        latency_ms=latency_ms,
        input_tokens=in_tok,
        output_tokens=out_tok,
        cost_usd=cost,
        calibrated=eng.calibrated,
        notes=notes,
    )


def _call_jev(state: Any, qobjs: dict[str, Any], *, model: str | None, timeout: float | None):
    from typesafe_sdk import TypeSafeClient

    with TypeSafeClient(model=model or JEV_DEFAULT_MODEL, timeout=timeout) as client:
        resp = client.system_one(state=state, questions=qobjs)
    return resp, model or JEV_DEFAULT_MODEL


def _call_adapter(state: Any, qobjs: dict[str, Any], *, model: str | None):
    from system_one_adapter import SystemOneAdapterClient
    from system_one_adapter.providers.openai import OpenAIProvider

    provider = OpenAIProvider(
        model_name=model or FALLBACK_MODEL,
        base_url=FALLBACK_BASE_URL,
        api_key=os.environ.get("DEEPSEEK_API_KEY"),
    )
    client = SystemOneAdapterClient(
        structured_outputs=False,  # DeepSeek: JSON prompt + istemci tarafı doğrulama
        llm_answer_mode="probabilities",
        normalize_probabilities=True,
        n_retry_malformed_structure=2,
        model=provider,
    )
    try:
        resp = client.system_one(state=state, questions=qobjs)
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()
    return resp, model or FALLBACK_MODEL


def _to_answer(qid: str, ans: Any, spec: Mapping[str, Any]) -> Answer:
    from .policy import gate

    atype = getattr(ans, "type", None) or spec.get("type", "unknown")
    probs = {str(k): float(v) for k, v in (getattr(ans, "probabilities", None) or {}).items()}
    legend = getattr(ans, "legend", None)
    conf = getattr(ans, "confidence", None)

    if atype == "noul":
        value: Any = float(getattr(ans, "noul", 0.0))
    elif atype == "choice":
        value = getattr(ans, "choice", None)
    elif atype == "score":
        value = float(getattr(ans, "score", 0.0))
    else:
        value = getattr(ans, "noul", None)

    a = Answer(
        id=qid,
        type=atype,
        value=value,
        confidence=None if conf is None else float(conf),
        probabilities=probs,
        legend=({str(k): v for k, v in dict(legend).items()} if isinstance(legend, Mapping) else None),
    )
    a.verdict = gate(a, spec.get("gate"))
    if atype == "noul":
        g = spec.get("gate") or {}
        yes_t = float(g.get("yes", 0.8))
        no_t = float(g.get("no", 0.2))
        a.yes = True if float(value) >= yes_t else (False if float(value) <= no_t else None)
    return a
