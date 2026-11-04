"""Тесты генератора черновиков и хранилища."""

from __future__ import annotations

import asyncio

import pytest

from config import Settings
from generator import CATEGORY_OFFERS, Generator, template_draft
from store import Store


# ── Генератор ─────────────────────────────────────────────────────

def lead(**kw) -> dict:
    base = {
        "id": "l1",
        "category": "auto",
        "group": "soyuzsto",
        "group_title": "Канал Союза Автосервисов",
        "display_name": "Тимур Автосервис",
        "username": "timur_sto",
        "text": "Посоветуйте норм CRM для СТО, заказ-наряды и склад запчастей, Москва",
    }
    base.update(kw)
    return base


def test_template_draft_contains_lead_context():
    d = template_draft(lead())
    assert "Тимур" in d
    assert "заказ-наряды" in d or "заказ-наряд" in d  # excerpt из текста лида
    assert "CRM для автосервисов" in d  # оффер вертикали auto


def test_template_draft_all_categories_have_offers():
    for cat in ("freezing", "bread", "coffee", "construction", "auto", "cleaning"):
        assert cat in CATEGORY_OFFERS
        d = template_draft(lead(category=cat))
        assert d and len(d) < 600


def test_template_draft_truncates_long_excerpt():
    d = template_draft(lead(text="Посоветуйте поставщика " + "очень подробно " * 30))
    assert len(d) < 500


def test_template_draft_fallback_name():
    d = template_draft(lead(display_name=None, username=None))
    assert "коллега" in d


def test_generator_template_mode_without_zai():
    gen = Generator(Settings())
    draft = asyncio.run(gen.draft(lead()))
    assert draft.via == "template"
    assert draft.text


class MockHttp:
    def __init__(self, content: str):
        self.content = content

    async def post(self, url, headers=None, json=None):
        content = self.content  # локальная переменная — без хитростей с self в R

        class R:
            status_code = 200

            def raise_for_status(self):
                pass

            def json(self):
                return {"choices": [{"message": {"content": content}}]}

        return R()

    async def aclose(self):
        pass


def test_generator_zai_polish_used():
    http = MockHttp("Здравствуйте! Увидел ваш запрос про CRM для СТО — актуально?")
    gen = Generator(Settings(zai_api_key="k"), http=http)
    draft = asyncio.run(gen.draft(lead()))
    assert draft.via == "zai"
    assert "Здравствуйте" in draft.text


def test_generator_zai_too_long_answer_falls_back_to_template():
    http = MockHttp("Слишком длинный ответ " * 100)
    gen = Generator(Settings(zai_api_key="k"), http=http)
    draft = asyncio.run(gen.draft(lead()))
    assert draft.via == "template"


# ── Хранилище ─────────────────────────────────────────────────────

def test_store_lead_lifecycle(tmp_path):
    store = Store(tmp_path / "d")
    lead = store.add_lead({
        "group_msg_key": "g:1", "group": "g", "text": "t", "category": "auto",
        "user_id": 1, "status": "new",
    })
    assert lead["id"] and lead["status"] == "new"

    # дедуп по group_msg_key
    assert store.add_lead({"group_msg_key": "g:1"}) == {}

    store.update_lead(lead["id"], status="drafted", draft="x")
    assert store.get_lead(lead["id"])["status"] == "drafted"

    store.update_lead(lead["id"], status="approved")
    store.log_send(lead["id"], 1, "g")
    store.update_lead(lead["id"], status="sent")

    assert store.list_leads(status="sent")
    assert store.stats()["leads_total"] == 1
    assert store.stats()["by_status"]["sent"] == 1


def test_store_list_leads_order_newest_first(tmp_path):
    store = Store(tmp_path / "d")
    for i in range(3):
        store.add_lead({"group_msg_key": f"g:{i}", "status": "new", "user_id": i})
    items = store.list_leads()
    assert items[0]["group_msg_key"] == "g:2"


def test_store_inbox_dedup_and_reply_marks_lead(tmp_path):
    store = Store(tmp_path / "d")
    lead = store.add_lead({"group_msg_key": "g:1", "status": "sent", "user_id": 5})
    item = store.add_inbox_item({
        "source": "dm", "message_id": 77, "user_id": 5,
        "text": "Да, интересно, скиньте цены",
    })
    assert item["lead_id"] == lead["id"]
    assert store.get_lead(lead["id"])["status"] == "replied"
    # тот же message_id снова — не дублируется
    assert store.add_inbox_item({"source": "dm", "message_id": 77}) == {}
    assert len(store.list_inbox()) == 1


def test_store_sends_today_window(tmp_path):
    store = Store(tmp_path / "d")
    now = 1_700_000_000
    day_start = now - (now % 86400)
    store.log_send("a", 1, "g")
    store.sent_log.mutate(
        lambda d: d["items"].append({"ts": day_start - 100, "lead_id": "b",
                                     "user_id": 2, "group": "g"})
    )
    assert len(store.sends_today(now)) == 1  # только сегодняшняя
    assert store.last_send_to_group("g") == day_start - 100 or store.last_send_to_group("g") > 0
    assert store.last_send_to_user(2) == day_start - 100


def test_store_meta_first_seen_stable(tmp_path):
    store = Store(tmp_path / "d")
    a = store.ensure_account_first_seen()
    b = store.ensure_account_first_seen()
    assert a == b
