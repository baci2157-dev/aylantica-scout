"""Классификатор лида: ZAI (GLM) с JSON-режимом + офлайн-эвристика.

Контракт один (LeadVerdict), реализаций две:
  * ZaiClassifier  — POST {base}/chat/completions, модель glm-4.6;
  * HeuristicClassifier — ключевые слова по категориям (тесты/офлайн).

Выбор: если есть ZAI_API_KEY — ZAI, при ошибке сети/парсинга — откат
на эвристику (с пометкой в reason).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict

from config import CATEGORY_HINTS, Settings, category_to_vertical


@dataclass
class LeadVerdict:
    is_lead: bool
    score: int          # 0..10
    reason: str
    business_type: str  # шаурмичная / пекарня / СТО / клининг-компания …
    vertical: str = ""  # вертикаль платформы: food_distribution / autoservice / services / non_food_distribution

    def to_dict(self) -> dict:
        return asdict(self)


SYSTEM_PROMPT = (
    "Ты — фильтр B2B-лидов для платформы CRM. Тебе дают текст сообщения "
    "из Telegram-чата бизнес-вертикали и категорию чата. Реши, это "
    "ЗАПРОС на поставку/подряд (лид) или шум (предложение, вакансия, "
    "флуд, реклама). Отвечай ТОЛЬКО валидным JSON без markdown: "
    '{"is_lead": bool, "score": 0-10, "reason": "кратко по-русски", '
    '"business_type": "тип бизнеса автора, если понятно, иначе ""}. '
    "Оцени: похоже ли, что автор — представитель бизнеса, которому нужен "
    "поставщик или подрядчик. score = 10 — явный тёплый запрос с объёмом "
    "и сроками; 5-7 — похоже на запрос; ниже — шум."
)


class HeuristicClassifier:
    """Офлайн-классификатор по ключевым словам категории."""

    def __init__(self, settings: Settings | None = None):
        self.settings = settings

    def classify(self, text: str, category: str) -> LeadVerdict:
        low = text.lower()
        hints = CATEGORY_HINTS.get(category, ())
        category_hits = sum(1 for h in hints if h in low)
        demand_hits = sum(
            1 for kw in ("нужен", "нужна", "ищу", "ищем", "посоветуйте",
                         "поставщик", "оптом", "подрядчик", "смета", "прайс")
            if kw in low
        )
        noise = any(kw in low for kw in ("продам", "продаю", "вакансия",
                                          "реклама", "казино", "займ"))
        volume = bool(re.search(r"\d{2,}", text))  # числа — объёмы/площади/сроки

        score = 0
        score += min(4, demand_hits * 2)
        score += min(3, category_hits)
        score += 2 if volume else 0
        score = max(0, min(10, score))

        is_lead = (not noise) and demand_hits >= 1 and score >= 4
        reason = (
            f"эвристика: спрос={demand_hits}, категория={category_hits}, "
            f"объёмы={volume}"
        )
        business_type = self._guess_business(low, category)
        return LeadVerdict(is_lead=is_lead, score=score,
                           reason=reason, business_type=business_type,
                           vertical=category_to_vertical(category))

    @staticmethod
    def _guess_business(low: str, category: str) -> str:
        pairs = (
            (("шаурм", "шаверм", "кебаб"), "шаурмичная"),
            (("кафе", "кофейн", "ресторан"), "кафе/ресторан"),
            (("пекарн", "кондитер"), "пекарня/кондитерская"),
            (("магазин", "продукт", "ритейл"), "магазин"),
            (("столов", "общепит", "food"), "столовая/общепит"),
            (("сто", "автосервис", "автомастер"), "автосервис"),
            (("клининг", "уборк"), "клининговая компания"),
            (("строй", "бригад", "прораб"), "строительная бригада"),
            (("офис", "компан"), "офис/компания"),
        )
        for keys, label in pairs:
            if any(k in low for k in keys):
                return label
        return {"freezing": "общепит", "bread": "пекарня", "coffee": "кофейня",
                "construction": "строители", "auto": "автосервис",
                "cleaning": "клининг"}.get(category, "")


class ZaiClassifier:
    """Классификатор через ZAI API (OpenAI-совместимый /chat/completions)."""

    def __init__(self, settings: Settings, http=None):
        """http: Injectable httpx.AsyncClient-подобный объект (для тестов)."""
        self.settings = settings
        self._http = http
        self._fallback = HeuristicClassifier(settings)

    async def _post_chat(self, text: str, category: str) -> dict:
        import httpx  # лениво: тесты без сети не тянут зависимость рантайма

        client = self._http or httpx.AsyncClient(timeout=30)
        own = self._http is None
        try:
            resp = await client.post(
                f"{self.settings.zai_base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.settings.zai_api_key}"},
                json={
                    "model": self.settings.zai_model,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user",
                         "content": f"Категория чата: {category}\nТекст:\n{text[:2000]}"},
                    ],
                    "temperature": 0.1,
                },
            )
            resp.raise_for_status()
            payload = resp.json()
            content = payload["choices"][0]["message"]["content"]
            return _parse_json_content(content)
        finally:
            if own:
                await client.aclose()

    async def classify(self, text: str, category: str) -> LeadVerdict:
        try:
            data = await self._post_chat(text, category)
            score = int(max(0, min(10, data.get("score", 0))))
            return LeadVerdict(
                is_lead=bool(data.get("is_lead", False)),
                score=score,
                reason=str(data.get("reason", ""))[:500] or "ZAI",
                business_type=str(data.get("business_type", ""))[:120],
                # вертикаль платформы всегда из маппинга категории —
                # LLM не выбирает вертикаль сам (детерминизм и покрытие)
                vertical=category_to_vertical(category),
            )
        except Exception as e:
            verdict = self._fallback.classify(text, category)
            verdict.reason = f"ZAI недоступен ({type(e).__name__}), эвристика: {verdict.reason}"
            return verdict


def _parse_json_content(content: str) -> dict:
    """Контент может прийти с ```json …``` или с префиксом — чистим."""
    s = content.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s, flags=re.S)
    start, end = s.find("{"), s.rfind("}")
    if start != -1 and end != -1:
        s = s[start : end + 1]
    return json.loads(s)


def make_classifier(settings: Settings) -> ZaiClassifier | HeuristicClassifier:
    """Фабрика: ZAI если есть ключ, иначе эвристика."""
    if settings.has_zai:
        return ZaiClassifier(settings)
    return HeuristicClassifier(settings)
