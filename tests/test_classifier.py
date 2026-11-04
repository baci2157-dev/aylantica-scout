"""Тесты классификатора: эвристика, ZAI (мок httpx), парсер JSON."""

from __future__ import annotations

import asyncio
import json

import pytest

from classifier import (
    HeuristicClassifier,
    LeadVerdict,
    ZaiClassifier,
    _parse_json_content,
    make_classifier,
)
from config import Settings


def settings(**kw) -> Settings:
    base = dict(zai_api_key="", zai_model="glm-4.6",
                zai_base_url="https://api.z.ai/api/paas/v4")
    base.update(kw)
    return Settings(**base)


# ── Эвристика ─────────────────────────────────────────────────────

def test_heuristic_warm_lead():
    v = HeuristicClassifier().classify(
        "Посоветуйте поставщика хлеба, пекарня в Казани, нужно 300 батонов в день, "
        "постоянно. Кто возит утром?", "bread"
    )
    assert isinstance(v, LeadVerdict)
    assert v.is_lead
    assert v.score >= 6
    assert v.business_type


def test_heuristic_noise_rejected():
    v = HeuristicClassifier().classify("Продам кофе в зёрнах, обжарка своя, прайс в лс", "coffee")
    assert not v.is_lead


def test_heuristic_score_bounded():
    v = HeuristicClassifier().classify(
        "Нужен поставщик опт подрядчик смета прайс посоветуйте срочно 500 кг каждый день "
        "кофе хлеб стройка уборка", "freezing"
    )
    assert 0 <= v.score <= 10


# ── ZAI (мок) ─────────────────────────────────────────────────────

class MockResponse:
    def __init__(self, payload: dict, status: int = 200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class MockHttp:
    """httpx.AsyncClient-мок: POST → ответ с заданным контентом."""

    def __init__(self, content: str | Exception):
        self.content = content
        self.calls = []

    async def post(self, url, headers=None, json=None):
        self.calls.append({"url": url, "json": json})
        if isinstance(self.content, Exception):
            raise self.content
        payload = {
            "choices": [{"message": {"role": "assistant", "content": self.content}}]
        }
        return MockResponse(payload)

    async def aclose(self):
        pass


ZAI_OK = json.dumps({
    "is_lead": True, "score": 8,
    "reason": "тёплый запрос: пекарня ищет поставщика муки",
    "business_type": "пекарня",
}, ensure_ascii=False)


def test_zai_classifies_lead():
    http = MockHttp(ZAI_OK)
    clf = ZaiClassifier(settings(zai_api_key="k"), http=http)
    v = asyncio.run(clf.classify("Ищу поставщика муки для пекарни", "bread"))
    assert v.is_lead and v.score == 8
    assert "пекарня" in v.business_type
    # проверяем, что запрос ушёл в правильный endpoint с ключом
    call = http.calls[0]
    assert call["url"].endswith("/chat/completions")
    assert "messages" in call["json"]


def test_zai_parses_markdown_fenced_json():
    http = MockHttp(f"```json\n{ZAI_OK}\n```")
    clf = ZaiClassifier(settings(zai_api_key="k"), http=http)
    v = asyncio.run(clf.classify("текст", "bread"))
    assert v.is_lead and v.score == 8


def test_zai_falls_back_on_network_error():
    http = MockHttp(RuntimeError("boom"))
    clf = ZaiClassifier(settings(zai_api_key="k"), http=http)
    v = asyncio.run(clf.classify("Посоветуйте поставщика хлеба для пекарни, 200 шт в день", "bread"))
    assert v.is_lead  # эвристика вытащила
    assert "эвристика" in v.reason


def test_zai_bad_json_falls_back():
    http = MockHttp("совсем не json")
    clf = ZaiClassifier(settings(zai_api_key="k"), http=http)
    v = asyncio.run(clf.classify("Нужен подрядчик на клининг офиса 400 м2", "cleaning"))
    assert "эвристика" in v.reason


def test_zai_score_clamped_to_10():
    content = json.dumps({"is_lead": True, "score": 99, "reason": "x", "business_type": ""})
    clf = ZaiClassifier(settings(zai_api_key="k"), http=MockHttp(content))
    v = asyncio.run(clf.classify("текст", "freezing"))
    assert v.score == 10


# ── Фабрика и парсер ──────────────────────────────────────────────

def test_make_classifier_without_key_is_heuristic():
    assert isinstance(make_classifier(settings()), HeuristicClassifier)


def test_make_classifier_with_key_is_zai():
    assert isinstance(make_classifier(settings(zai_api_key="k")), ZaiClassifier)


def test_parse_json_content_variants():
    assert _parse_json_content('{"a": 1}') == {"a": 1}
    assert _parse_json_content('```json\n{"a": 2}\n```') == {"a": 2}
    assert _parse_json_content('префикс {"a": 3} суффикс') == {"a": 3}
