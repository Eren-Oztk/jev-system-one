"""Eşikler ve karar politikası — TypeSafe 'Confidence' + 'noul okuma' rehberine göre.

Üç yol (docs/confidence):
  act      -> otomatik devam
  review   -> işaretle / insana sor
  escalate -> karar verme, insana yönlendir

Noul: eşikler `gate.yes` / `gate.no` (varsayılan 0.8 / 0.2), arada kalan "review".
Choice/Score: `gate.act` / `gate.review` (varsayılan 0.7 / 0.4) confidence üzerinden.

UYARI: Choice/Score confidence yalnızca dağılımın tepkisini özetler; iş akışının
doğruluğu ya da "işlem yapma izni" değildir. Noul ~0.5 "orta şiddet" değil,
"evet/hayır eşit olasılık" demektir.
"""

from __future__ import annotations

from typing import Any, Mapping

DEFAULT_NOUL_YES = 0.8
DEFAULT_NOUL_NO = 0.2
DEFAULT_CHOICE_ACT = 0.7
DEFAULT_CHOICE_REVIEW = 0.4
DEFAULT_SCORE_ACT = 0.7
DEFAULT_SCORE_REVIEW = 0.4

_RANK = {"act": 0, "review": 1, "escalate": 2}


def gate(answer: Any, gate_spec: Mapping[str, Any] | None) -> str:
    g = dict(gate_spec or {})
    if answer.type == "noul":
        yes = float(g.get("yes", DEFAULT_NOUL_YES))
        no = float(g.get("no", DEFAULT_NOUL_NO))
        if yes <= no:
            raise ValueError("gate.yes, gate.no'dan büyük olmalı")
        v = float(answer.value)
        if v >= yes or v <= no:
            return "act"
        return "review"
    act = float(g.get("act", DEFAULT_CHOICE_ACT))
    review = float(g.get("review", DEFAULT_CHOICE_REVIEW))
    conf = answer.confidence
    if conf is None:
        return "review"
    if conf >= act:
        return "act"
    if conf >= review:
        return "review"
    return "escalate"


def overall_verdict(
    answers: Mapping[str, Any],
    specs: Mapping[str, Mapping[str, Any]] | None = None,
) -> str:
    """En kötü verdict kazanır; pack içindeki ek kurallar da uygulanır."""
    worst = "act"
    for a in answers.values():
        if _RANK[a.verdict] > _RANK[worst]:
            worst = a.verdict

    for rule in _rules(specs):
        if _match(rule, answers) and _RANK[rule.get("then", "escalate")] > _RANK[worst]:
            worst = rule.get("then", "escalate")
    return worst


def _rules(specs: Mapping[str, Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    """Policy pack'ten gelirse `_policy` anahtarında taşınır; yoksa boş."""
    if not specs:
        return []
    policy = specs.get("_policy") if isinstance(specs, Mapping) else None
    if not isinstance(policy, Mapping):
        return []
    rules = policy.get("rules") or []
    return [dict(r) for r in rules if isinstance(r, Mapping)]


def _match(rule: Mapping[str, Any], answers: Mapping[str, Any]) -> bool:
    qid = rule.get("question")
    op = rule.get("op", ">")
    target = rule.get("value")
    a = answers.get(qid)
    if a is None:
        return False
    val = a.value
    try:
        if op in (">", ">=", "<", "<="):
            val_f, target_f = float(val), float(target)
            return {
                ">": val_f > target_f,
                ">=": val_f >= target_f,
                "<": val_f < target_f,
                "<=": val_f <= target_f,
            }[op]
        if op == "==":
            return str(val) == str(target)
        if op == "!=":
            return str(val) != str(target)
        if op == "in":
            return str(val) in [str(x) for x in target]
    except (TypeError, ValueError):
        return False
    return False
