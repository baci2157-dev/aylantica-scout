"""Генератор черновиков первого касания.

Два слоя:
  1) детерминированный шаблон вертикали (всегда, офлайн, тестируемый);
  2) LLM-полировка через ZAI (если ключ задан) — «по-человечески»,
     коротко, без ссылок в прогрев (см. sender.strip_links_for_warmup).

Черновик — это ПРЕДЛОЖЕНИЕ о разговоре, не прайс-лист:
пара строк, конкретика из сообщения лида, один вопрос.
"""

from __future__ import annotations

from dataclasses import dataclass

from config import Settings

# Готовые офферы платформы по вертикалям (что предлагаем автору запроса).
CATEGORY_OFFERS: dict[str, dict[str, str]] = {
    "freezing": {
        "product": "картофель фри и заморозку напрямую от дистрибьютора",
        "hook": "цены от дистрибьютора, доставка на точку, отсрочка для постоянных",
    },
    "bread": {
        "product": "свежий хлеб и выпечку с производства",
        "hook": "утренняя доставка до открытия, возврат непроданного по договору",
    },
    "coffee": {
        "product": "кофе в зёрнах для кофеен и обжарку под вашу карту",
        "hook": "тестовый пакет бесплатно, обучение бариста в подарок",
    },
    "construction": {
        "product": "CRM для строительных бригад: сметы, объекты, материалы",
        "hook": "смета и учёт объекта в телефоне, без экселя",
    },
    "auto": {
        "product": "CRM для автосервисов: заказ-наряды, склад запчастей, клиенты",
        "hook": "записи и перезвоны не теряются, склад под контролем",
    },
    "cleaning": {
        "product": "CRM для клининговых компаний: заявки, бригады, графики",
        "hook": "заявки и объекты в одном экране, без блокнотов",
    },
}


@dataclass
class Draft:
    text: str
    via: str  # "template" | "zai"


TEMPLATE = (
    "Здравствуйте, {name}! Увидел(а) ваше сообщение в «{group}»: {excerpt}. "
    "Мы как раз занимаемся {product} — {hook}. "
    "Актуально ли для вас? Если да — скину цены и условия."
)

POLISH_PROMPT = (
    "Перепиши сообщение первого касания по-человечески: 2-3 коротких "
    "предложения, живой тон, без канцелярита, без ссылок, без слова "
    "'коммерческое предложение'. Сохрани суть и вопрос в конце. "
    "Ответь только текстом сообщения.\n\n---\n{draft}"
)


def template_draft(lead: dict) -> str:
    """Детерминированный черновик из шаблона вертикали."""
    category = lead.get("category", "")
    offer = CATEGORY_OFFERS.get(category, {
        "product": "поставки для бизнеса вашего профиля",
        "hook": "выгодные условия для новых клиентов",
    })
    name = (lead.get("display_name") or lead.get("username") or "коллега").split()[0]
    excerpt = (lead.get("text") or "").strip().replace("\n", " ")
    if len(excerpt) > 90:
        excerpt = excerpt[:90].rsplit(" ", 1)[0] + "…"
    return TEMPLATE.format(
        name=name,
        group=lead.get("group_title") or lead.get("group", "чате"),
        excerpt=excerpt,
        product=offer["product"],
        hook=offer["hook"],
    )


class Generator:
    """Черновик: шаблон + опциональная LLM-полировка (ZAI)."""

    def __init__(self, settings: Settings, http=None):
        self.settings = settings
        self._http = http

    async def draft(self, lead: dict) -> Draft:
        base = template_draft(lead)
        if not self.settings.has_zai:
            return Draft(text=base, via="template")
        try:
            polished = await self._polish(base)
            if polished is None:
                return Draft(text=base, via="template")
            return Draft(text=polished, via="zai")
        except Exception:
            return Draft(text=base, via="template")

    async def _polish(self, base: str) -> str | None:
        """LLM-полировка. None — ответ негоден (длинный/мусорный) → шаблон."""
        import httpx

        client = self._http or httpx.AsyncClient(timeout=30)
        own = self._http is None
        try:
            resp = await client.post(
                f"{self.settings.zai_base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.settings.zai_api_key}"},
                json={
                    "model": self.settings.zai_model,
                    "messages": [
                        {"role": "system",
                         "content": "Ты — редактор деловой переписки в Telegram."},
                        {"role": "user", "content": POLISH_PROMPT.format(draft=base)},
                    ],
                    "temperature": 0.6,
                },
            )
            resp.raise_for_status()
            payload = resp.json()
            text = payload["choices"][0]["message"]["content"].strip()
            # страховка: LLM иногда любит дописать пояснения
            if len(text) > 600 or "```" in text or not text:
                return None
            return text
        finally:
            if own:
                await client.aclose()
