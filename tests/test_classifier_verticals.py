"""Блок 2 (v2.2): маппинг категорий Scout → вертикали платформы.

6 категорий → 4 вертикали платформы (полное покрытие):
  freezing, bread, coffee → food_distribution
  auto                    → autoservice
  cleaning                → services
  construction            → non_food_distribution

Проверяем: маппинг в config, вертикаль в вердикте классификатора
(эвристика и ZAI-ветка), вертикаль в payload пуша в Aylantica.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from classifier import HeuristicClassifier, ZaiClassifier, LeadVerdict  # noqa: E402
from config import (  # noqa: E402
    CATEGORY_HINTS,
    CATEGORY_LABELS,
    CATEGORY_TO_VERTICAL,
    Settings,
    VERTICAL_LABELS,
    category_label,
    category_to_vertical,
)

# 6 категорий → 6 ожидаемых вертикалей (значения платформы Aylantica)
EXPECTED = {
    "freezing": "food_distribution",
    "bread": "food_distribution",
    "coffee": "food_distribution",
    "auto": "autoservice",
    "cleaning": "services",
    "construction": "non_food_distribution",
}

DEMAND_TEXT = (
    "Нужен поставщик на постоянку, объём приличный, прайс пришлите"
)


class TestMapping:
    def test_six_categories_six_verticals(self):
        """Ядро постановки: 6 категорий → полная карта вертикалей."""
        assert CATEGORY_TO_VERTICAL == EXPECTED

    def test_every_category_has_vertical(self):
        for cat in CATEGORY_HINTS:
            assert cat in CATEGORY_TO_VERTICAL, f"нет вертикали для {cat}"

    def test_only_known_platform_verticals(self):
        allowed = {"food_distribution", "autoservice", "services", "non_food_distribution"}
        assert set(CATEGORY_TO_VERTICAL.values()) <= allowed

    def test_unknown_category_defaults_to_services(self):
        assert category_to_vertical("nope") == "services"
        assert category_to_vertical("") == "services"

    @pytest.mark.parametrize("cat,vertical", sorted(EXPECTED.items()))
    def test_mapping_pairs(self, cat, vertical):
        assert category_to_vertical(cat) == vertical

    def test_labels_present(self):
        for cat in CATEGORY_TO_VERTICAL:
            assert cat in CATEGORY_LABELS
            assert category_label(cat)
        for vertical in set(EXPECTED.values()):
            assert vertical in VERTICAL_LABELS


class TestClassifierVertical:
    def setup_method(self):
        self.clf = HeuristicClassifier(Settings())

    @pytest.mark.parametrize("cat,vertical", sorted(EXPECTED.items()))
    def test_heuristic_verdict_carries_vertical(self, cat, vertical):
        verdict = self.clf.classify(DEMAND_TEXT, cat)
        assert isinstance(verdict, LeadVerdict)
        assert verdict.vertical == vertical

    def test_verdict_dict_contains_vertical(self):
        verdict = self.clf.classify(DEMAND_TEXT, "auto")
        assert verdict.to_dict()["vertical"] == "autoservice"

    def test_zai_verdict_vertical_even_on_bad_json(self):
        """ZAI-ветка: вертикаль ставится из маппинга, даже если LLM молчит/ломается."""

        class BrokenHttp:
            async def post(self, *a, **kw):
                raise RuntimeError("network down")

            async def aclose(self):
                pass

        clf = ZaiClassifier(Settings(zai_api_key="k"), http=BrokenHttp())
        import asyncio

        verdict = asyncio.run(clf.classify(DEMAND_TEXT, "auto"))
        assert verdict.vertical == "autoservice"

    def test_zai_verdict_vertical_from_mapping_not_llm(self):
        """LLM не выбирает вертикаль: даже если он вернёт мусор — маппинг."""

        class FakeHttp:
            async def post(self, *a, **kw):
                class R:
                    def raise_for_status(self):
                        pass

                    def json(self):
                        return {"choices": [{"message": {"content": '{"is_lead": true, "score": 9, "reason": "x", "business_type": "СТО", "vertical": "wrong_vertical"}'}}]}

                return R()

            async def aclose(self):
                pass

        clf = ZaiClassifier(Settings(zai_api_key="k"), http=FakeHttp())
        import asyncio

        verdict = asyncio.run(clf.classify(DEMAND_TEXT, "cleaning"))
        assert verdict.vertical == "services"  # маппинг, а не ответ LLM


class TestPushPayload:
    def test_payload_contains_vertical(self):
        """Пуш в Aylantica несёт vertical (маппинг по категории)."""
        from main import push_to_aylantica  # noqa: WPS433
        import asyncio

        captured = {}

        class Resp:
            def raise_for_status(self):
                pass

            def json(self):
                return {"ok": True, "created": True, "package": {}}

        class FakeClient:
            def __init__(self, *a, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                pass

            async def post(self, url, json=None, headers=None):
                captured["url"] = url
                captured["json"] = json
                captured["headers"] = headers
                return Resp()

        import httpx

        orig = httpx.AsyncClient
        httpx.AsyncClient = FakeClient
        try:
            import main as main_mod

            main_mod.SETTINGS = Settings(
                aylantica_api_url="https://example.com",
                aylantica_scout_secret="s3cret",
            )
            lead = {
                "id": "ld-1",
                "category": "auto",
                "business_type": "автосервис",
                "city": "Москва",
                "text": DEMAND_TEXT,
                "username": "u",
                "score": 8,
            }
            out = asyncio.run(push_to_aylantica(lead))
        finally:
            httpx.AsyncClient = orig

        assert out is not None
        payload = captured["json"]
        assert captured["url"].endswith("/api/scout/leads")
        assert payload["vertical"] == "autoservice"
        assert payload["category"] == "auto"

    def test_payload_vertical_falls_back_to_mapping(self):
        """Если у лида нет поля vertical — считается по категории."""
        assert category_to_vertical("bread") == "food_distribution"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
