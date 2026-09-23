"""Ağsız birim testler: eşikleme, verdict, soru doğrulama, pack yükleme."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from systemone.client import Answer, SystemOneError, build_question, build_questions
from systemone.packs import list_packs, load_pack
from systemone.policy import gate, overall_verdict


def _ans(**kw):
    base = dict(id="q", type="noul", value=0.9)
    base.update(kw)
    a = Answer(**base)
    a.verdict = gate(a, base.pop("gate", None))
    return a


# ---------------------------------------------------------------- noul eşikleri
def test_noul_thresholds_three_way():
    assert gate(_ans(type="noul", value=0.95), None) == "act"
    assert gate(_ans(type="noul", value=0.05), None) == "act"
    assert gate(_ans(type="noul", value=0.5), None) == "review"
    assert gate(_ans(type="noul", value=0.79), None) == "review"
    assert gate(_ans(type="noul", value=0.81), None) == "act"


def test_noul_custom_gate():
    assert gate(_ans(type="noul", value=0.5), {"yes": 0.5, "no": 0.1}) == "act"
    with pytest.raises(ValueError):
        gate(_ans(type="noul", value=0.5), {"yes": 0.2, "no": 0.8})


# ------------------------------------------------------------ choice/score eşik
def test_choice_confidence_gate():
    assert gate(_ans(type="choice", value="a", confidence=0.9), None) == "act"
    assert gate(_ans(type="choice", value="a", confidence=0.5), None) == "review"
    assert gate(_ans(type="choice", value="a", confidence=0.1), None) == "escalate"
    assert gate(_ans(type="choice", value="a", confidence=None), None) == "review"


def test_score_confidence_gate():
    assert gate(_ans(type="score", value=1.4, confidence=0.85), None) == "act"
    assert gate(_ans(type="score", value=1.4, confidence=0.3), None) == "escalate"


# -------------------------------------------------------------------- verdict
def test_overall_verdict_worst_wins():
    answers = {
        "a": _ans(id="a", type="choice", value="x", confidence=0.95),
        "b": _ans(id="b", type="noul", value=0.5),  # review
    }
    assert overall_verdict(answers, None) == "review"


def test_policy_rules_apply():
    answers = {"frustration": _ans(id="frustration", type="score", value=2.4, confidence=0.9)}
    specs = {
        "frustration": {"type": "score"},
        "_policy": {"rules": [{"question": "frustration", "op": ">=", "value": 2.0, "then": "review"}]},
    }
    assert overall_verdict(answers, specs) == "review"


def test_policy_ops():
    answers = {
        "x": _ans(id="x", type="noul", value=0.9),
        "y": _ans(id="y", type="choice", value="billing", confidence=0.9),
    }
    specs = {"_policy": {"rules": [{"question": "y", "op": "==", "value": "billing", "then": "escalate"}]}}
    assert overall_verdict(answers, specs) == "escalate"
    specs2 = {"_policy": {"rules": [{"question": "y", "op": "in", "value": ["sales"], "then": "escalate"}]}}
    assert overall_verdict(answers, specs2) == "act"
    # bilinmeyen soru adı sessizce eşleşmez
    specs3 = {"_policy": {"rules": [{"question": "yok", "op": ">", "value": 0, "then": "escalate"}]}}
    assert overall_verdict(answers, specs3) == "act"


# -------------------------------------------------------------- soru inşası
def test_build_question_validation():
    with pytest.raises(SystemOneError):
        build_question({"type": "noul"})  # instructions yok
    with pytest.raises(SystemOneError):
        build_question({"type": "choice", "instructions": "x", "criteria": {}})
    with pytest.raises(SystemOneError):
        build_question({"type": "score", "instructions": "x", "criteria": ["tek"]})
    with pytest.raises(SystemOneError):
        build_question({"type": "score", "instructions": "x", "criteria": [str(i) for i in range(11)]})
    with pytest.raises(SystemOneError):
        build_question({"type": "yok", "instructions": "x"})


def test_build_questions_skips_policy():
    qs = build_questions(
        {
            "a": {"type": "noul", "instructions": "x", "gate": {"yes": 0.9}},
            "_policy": {"rules": []},
        }
    )
    assert set(qs) == {"a"}
    assert qs["a"].model_dump()["type"] == "noul"


def test_choice_null_description_allowed():
    q = build_question({"type": "choice", "instructions": "x", "criteria": {"a": None, "b": "B"}})
    assert set(q.model_dump()["criteria"]) == {"a", "b"}


# ------------------------------------------------------------------ pack'ler
def test_all_builtin_packs_load_and_validate():
    packs = list_packs()
    assert {"support_triage", "lead_qualify", "llm_guardrail", "hermes_route"} <= set(packs)
    for name in packs:
        data = load_pack(name)
        assert data["description"]
        build_questions(data["questions"])  # hepsi geçerli olmalı
        for qid, spec in data["questions"].items():
            if qid.startswith("_"):
                continue
            assert spec.get("type") in ("noul", "choice", "score"), qid
            assert spec.get("instructions"), qid


def test_user_pack_overrides_builtin(tmp_path, monkeypatch):
    user = tmp_path / "packs"
    user.mkdir()
    (user / "support_triage.json").write_text(
        json.dumps({"questions": {"x": {"type": "noul", "instructions": "kişisel"}}}), encoding="utf-8"
    )
    monkeypatch.setattr("systemone.packs.USER_DIR", user)
    assert load_pack("support_triage")["questions"]["x"]["instructions"] == "kişisel"


def test_pack_missing_raises():
    with pytest.raises(FileNotFoundError):
        load_pack("boyle-bir-pack-yok")
