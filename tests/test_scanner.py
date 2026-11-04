"""Тесты сканера: фильтр спроса, чтение мок-групп, дедуп-ключи."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pytest

from config import GroupEntry, Settings
from scanner import RawPost, Scanner, extract_city, looks_like_demand, message_url


# ── Фикстуры-моки ─────────────────────────────────────────────────

@dataclass
class FakeUser:
    id: int = 1
    username: str | None = "chef_ivan"
    first_name: str | None = "Иван"
    last_name: str | None = "Петров"


@dataclass
class FakeMsg:
    id: int = 1
    text: str | None = ""
    date: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))
    sender: FakeUser = field(default_factory=FakeUser)


class FakeEntity:
    def __init__(self, title: str):
        self.title = title


class FakeClient:
    """Telethon-подобный клиент: get_entity + iter_messages."""

    def __init__(self, msgs: list[FakeMsg], title: str = "Test Group"):
        self.msgs = msgs
        self.entity = FakeEntity(title)

    async def get_entity(self, ref: str):
        if ref == "broken":
            raise RuntimeError("no entity")
        return self.entity

    def iter_messages(self, entity, limit: int = 100):
        msgs = self.msgs[:limit]

        async def gen():
            for m in msgs:
                yield m

        return gen()


GROUP = GroupEntry(username="foodovik", name="Foodovik business", category="freezing")


def settings(**kw) -> Settings:
    base = dict(telegram_api_id=1, telegram_api_hash="x", telegram_session="s",
                scan_window_h=12, scan_messages_limit=100)
    base.update(kw)
    return Settings(**base)


# ── looks_like_demand ─────────────────────────────────────────────

def test_demand_obvious_query():
    assert looks_like_demand("Посоветуйте поставщика картофеля фри для кафе, нужно 200 кг в неделю")


def test_demand_short_text_rejected():
    assert not looks_like_demand("нужен опт")  # < 15 символов


def test_demand_single_weak_word_not_enough():
    assert not looks_like_demand("Привет всем, кто тут живёт, как дела в чате?")


def test_demand_noise_sale_rejected():
    assert not looks_like_demand("Продам картофель фри оптом, дёшево, от 100 кг, доставка")


def test_demand_noise_vacancy_rejected():
    assert not looks_like_demand("Вакансия: нужен повар в столовую, Москва, график 2/2")


def test_demand_strong_keyword_passes():
    assert looks_like_demand("Кто возьмёт подряд на уборку офиса 400 м²? Нужен исполнитель срочно")


# ── Scanner.scan_group ────────────────────────────────────────────

def test_scan_group_filters_demand_and_fields():
    msgs = [
        FakeMsg(id=1, text="Посоветуйте поставщика заморозки, нужно 3 короба в неделю",
                sender=FakeUser(id=10, username="ivan")),
        FakeMsg(id=2, text="Продам соусы оптом", sender=FakeUser(id=11)),  # шум
        FakeMsg(id=3, text=None, sender=FakeUser(id=12)),  # без текста
    ]
    client = FakeClient(msgs)
    sc = Scanner(client, settings())
    since = 0
    posts = asyncio.run(sc.scan_group(GROUP, since))
    assert len(posts) == 1
    p = posts[0]
    assert isinstance(p, RawPost)
    assert p.user_id == 10
    assert p.username == "ivan"
    assert p.display_name == "Иван Петров"
    assert p.category == "freezing"
    assert p.group_msg_key == "foodovik:1"


def test_scan_group_since_ts_cuts_old():
    old = FakeMsg(id=1, text="Посоветуйте поставщика картофеля фри для кафе, срочно",
                  date=datetime(2020, 1, 1, tzinfo=timezone.utc))
    fresh = FakeMsg(id=2, text="Посоветуйте поставщика картофеля фри для кафе, срочно")
    client = FakeClient([old, fresh])
    sc = Scanner(client, settings())
    since = int(datetime.now(tz=timezone.utc).timestamp()) - 3600
    posts = asyncio.run(sc.scan_group(GROUP, since))
    assert len(posts) == 1 and posts[0].message_id == 2


def test_scan_group_entity_error_returns_empty():
    sc = Scanner(FakeClient([]), settings())
    broken = GroupEntry(username="broken", name="X", category="freezing")
    posts = asyncio.run(sc.scan_group(broken, 0))
    assert posts == []


def test_scan_groups_error_does_not_break_others():
    client = FakeClient([FakeMsg(id=5, text="Ищем подрядчика на клининг офиса, 300 м²")])
    sc = Scanner(client, settings())
    groups = [
        GroupEntry(username="broken", name="X", category="cleaning"),
        GroupEntry(username="ok", name="Ok", category="cleaning"),
    ]
    posts = asyncio.run(sc.scan_groups(groups, 0))
    assert len(posts) == 1 and posts[0].group == "ok"


def test_scan_groups_progress_called():
    client = FakeClient([FakeMsg(id=5, text="Ищем подрядчика на клининг офиса, 300 м²")])
    sc = Scanner(client, settings())
    seen = []

    async def progress(group: str, n: int):
        seen.append((group, n))

    asyncio.run(sc.scan_groups([GROUP], 0, progress=progress))
    assert seen == [("foodovik", 1)]


# ── Утилиты ───────────────────────────────────────────────────────

def test_message_url():
    assert message_url("foodovik", 42) == "https://t.me/foodovik/42"


def test_extract_city_known():
    assert extract_city("Нужно снабжение объекта, Москва, 200 кг") == "Москва"


def test_extract_city_in_gorod():
    assert extract_city("Ищем поставщика в городе Казань на постоянку") == "Казань"


def test_extract_city_none():
    assert extract_city("Кто возьмёт подряд?") is None
