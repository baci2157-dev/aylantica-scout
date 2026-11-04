"""Тесты отправщика: гейт лимитов, прогрев без ссылок, лог отправок."""

from __future__ import annotations

import asyncio
import time

import pytest

from config import Settings
from sender import Sender, strip_links_for_warmup
from store import Store


class FakeClient:
    def __init__(self):
        self.sent: list[tuple[object, str]] = []

    async def send_message(self, ref, text: str):
        self.sent.append((ref, text))


def make_sender(tmp_path, clock, **kw) -> tuple[Sender, Store, FakeClient]:
    base = dict(
        daily_send_limit=2,
        send_interval_sec=100,
        warmup_no_link_days=2,
        group_cooldown_h=4.0,
        user_cooldown_days=14,
    )
    base.update(kw)
    store = Store(tmp_path / "data", clock=clock)
    client = FakeClient()
    return Sender(client, Settings(**base), store, clock=clock), store, client


def put(store: Store, lead: dict) -> dict:
    """Положить лида в store (как это делает /scan) — с апселом статуса."""
    existing = store.get_lead(lead["id"])
    if existing:
        return store.update_lead(lead["id"], **{k: v for k, v in lead.items() if k != "id"}) or existing
    return store.add_lead({
        "group_msg_key": f"g:{lead['id']}", "group": lead.get("group", "g"),
        "user_id": lead.get("user_id", 1), "status": lead.get("status", "approved"),
        "draft": lead.get("draft", ""), **lead,
    })


def approved_lead(**kw) -> dict:
    lead = {
        "id": "lead1",
        "status": "approved",
        "group": "foodovik",
        "user_id": 10,
        "username": "ivan",
        "draft": "Здравствуйте! Пишем по вашему запросу: цены скину в ответ.",
    }
    lead.update(kw)
    return lead


# ── Гейт ──────────────────────────────────────────────────────────

def test_send_ok_first_time(tmp_path):
    t = [1_000_000]
    sender, store, client = make_sender(tmp_path, lambda: t[0])
    lead = approved_lead()
    put(store, lead)
    ok, reason = asyncio.run(sender.send(lead))
    assert ok, reason
    assert len(client.sent) == 1
    stored = store.get_lead("lead1")
    assert stored["status"] == "sent"
    assert store.sends_today() and store.sends_today()[0]["user_id"] == 10


def test_gate_requires_approved_status(tmp_path):
    t = [1_000_000]
    sender, _, client = make_sender(tmp_path, lambda: t[0])
    ok, reason = asyncio.run(sender.send(approved_lead(status="drafted")))
    assert not ok and "approved" in reason
    assert not client.sent


def test_gate_daily_limit(tmp_path):
    t = [1_000_000]
    sender, store, _ = make_sender(tmp_path, lambda: t[0], daily_send_limit=1,
                                   group_cooldown_h=0.0)
    l1, l2 = approved_lead(id="l1", user_id=1), approved_lead(id="l2", user_id=2)
    put(store, l1); put(store, l2)
    assert asyncio.run(sender.send(l1))[0]
    ok, reason = asyncio.run(sender.send(l2))
    assert not ok and "дневной лимит" in reason


def test_gate_interval_between_sends(tmp_path):
    t = [1_000_000]
    sender, store, _ = make_sender(tmp_path, lambda: t[0], send_interval_sec=100,
                                    group_cooldown_h=0.0)
    l1 = approved_lead(id="l1", user_id=1)
    l2 = approved_lead(id="l2", user_id=2)
    put(store, l1); put(store, l2)
    assert asyncio.run(sender.send(l1))[0]
    t[0] += 40  # прошло 40 сек < 100
    ok, reason = asyncio.run(sender.send(l2))
    assert not ok and "интервал" in reason
    t[0] += 61  # итого 101 сек ≥ 100
    assert asyncio.run(sender.send(l2))[0]


def test_gate_group_cooldown(tmp_path):
    t = [1_000_000]
    sender, store, _ = make_sender(tmp_path, lambda: t[0], group_cooldown_h=4.0,
                                    send_interval_sec=0, daily_send_limit=10)
    l1 = approved_lead(id="l1", user_id=1, group="g1")
    l2 = approved_lead(id="l2", user_id=2, group="g2")
    l3 = approved_lead(id="l3", user_id=3, group="g1")
    put(store, l1); put(store, l2); put(store, l3)
    assert asyncio.run(sender.send(l1))[0]
    t[0] += 3600  # 1 час < 4 ч, но другая группа — ок
    assert asyncio.run(sender.send(l2))[0]
    # ту же группу раньше 4 ч — нельзя
    ok, reason = asyncio.run(sender.send(l3))
    assert not ok and "кулдаун группы" in reason


def test_gate_user_cooldown(tmp_path):
    t = [1_000_000]
    sender, store, _ = make_sender(tmp_path, lambda: t[0], user_cooldown_days=14,
                                    send_interval_sec=0, group_cooldown_h=0.0)
    l1 = approved_lead(id="l1", user_id=7)
    l2 = approved_lead(id="l2", user_id=7)
    put(store, l1); put(store, l2)
    assert asyncio.run(sender.send(l1))[0]
    t[0] += 5 * 86400  # 5 дней < 14
    ok, reason = asyncio.run(sender.send(l2))
    assert not ok and "этому человеку" in reason


# ── Прогрев: без ссылок ───────────────────────────────────────────

def test_warmup_strips_links(tmp_path):
    t = [1_000_000]
    sender, store, client = make_sender(tmp_path, lambda: t[0], warmup_no_link_days=2,
                                        send_interval_sec=0)
    lead = approved_lead(
        draft="Здравствуйте! Смотрите наш сайт https://example.com/pic прайс там."
    )
    put(store, lead)
    ok, _ = asyncio.run(sender.send(lead))
    assert ok
    sent_text = client.sent[0][1]
    assert "http" not in sent_text and "example.com" not in sent_text


def test_after_warmup_links_kept(tmp_path):
    t = [1_000_000]
    sender, store, client = make_sender(tmp_path, lambda: t[0], warmup_no_link_days=2,
                                        send_interval_sec=0)
    # аккаунт «живёт» уже 3 дня: first_seen фиксируем в прошлом
    sender.store.ensure_account_first_seen()
    sender.store.meta.mutate(
        lambda d: d.update({"account_first_seen": t[0] - 3 * 86400})
    )
    lead = approved_lead(draft="Прайс: https://example.com/prices")
    put(store, lead)
    ok, _ = asyncio.run(sender.send(lead))
    assert ok and "https://example.com/prices" in client.sent[0][1]


def test_strip_links_for_warmup_util():
    text, had = strip_links_for_warmup("сайт https://a.ru/x и ещё b.com/hi тут")
    assert had
    assert "a.ru" not in text and "b.com" not in text


# ── Отправка падает мягко ─────────────────────────────────────────

class BoomClient(FakeClient):
    async def send_message(self, ref, text):
        raise RuntimeError("FloodWait")


def test_send_telegram_error_reported(tmp_path):
    t = [1_000_000]
    store = Store(tmp_path / "data", clock=lambda: t[0])
    sender = Sender(BoomClient(), Settings(daily_send_limit=2, send_interval_sec=0),
                    store, clock=lambda: t[0])
    lead = approved_lead()
    put(store, lead)
    ok, reason = asyncio.run(sender.send(lead))
    assert not ok and "FloodWait" in reason
    assert not store.sends()  # лог не пишем — отправки не было


def test_send_without_draft_refused(tmp_path):
    t = [1_000_000]
    sender, store, client = make_sender(tmp_path, lambda: t[0])
    lead = approved_lead(draft="")
    put(store, lead)
    ok, reason = asyncio.run(sender.send(lead))
    assert not ok and "черновик" in reason
    assert not client.sent
