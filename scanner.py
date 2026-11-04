"""Сканер: Telethon читает группы/каналы → сырые посты-кандидаты.

Слой тонкий и полностью мокается: scanner зависит только от «клиента»
с методом iter_messages (как у Telethon) — тесты подсовывают фейкового.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from config import (
    DEMAND_KEYWORDS,
    NOISE_KEYWORDS,
    Settings,
)

# Грубый фильтр: 2+ вхождения demand-слов или 1 сильное («поставщик», «оптом»…)
STRONG_KEYWORDS = ("поставщик", "поставщиков", "опт", "оптом", "куплю",
                   "подряд", "исполнител", "посоветуйте", "порекомендуйте",
                   "снабжен")


@dataclass
class RawPost:
    """Сообщение-кандидат из площадки."""

    group: str                 # username площадки
    group_title: str
    message_id: int
    user_id: int
    username: str | None
    display_name: str | None
    text: str
    posted_at: int             # unix ts
    category: str = ""
    group_msg_key: str = field(default="")

    def __post_init__(self) -> None:
        if not self.group_msg_key:
            self.group_msg_key = f"{self.group}:{self.message_id}"


def looks_like_demand(text: str) -> bool:
    """Быстрый локальный фильтр до классификатора.

    Убирает очевидный шум (продажа/вакансии/реклама) и пропускает
    только похоже-на-запрос тексты. Осознанно грубый: точностью
    занимается classifier (ZAI), здесь — экономия токенов.
    """
    if not text or len(text) < 15:
        return False
    low = text.lower()

    hits = sum(1 for kw in DEMAND_KEYWORDS if kw in low)
    strong = any(kw in low for kw in STRONG_KEYWORDS)
    if hits < 2 and not strong:
        return False
    if any(kw in low for kw in NOISE_KEYWORDS):
        return False
    # продажа со стороны автора («продам») уже в шуме; здесь — только спрос
    return True


def _invite_or_username_error(e: Exception) -> str:
    return f"{type(e).__name__}: {e}"


class Scanner:
    """Чтение площадок через Telethon-подобный клиент.

    client: объект с `iter_messages(entity, limit)` (async iterable),
    как telegram.client.TelegramClient. Энтити — строка username или
    invite-ссылка; joining не делает (см. join_group).
    """

    def __init__(self, client: Any, settings: Settings):
        self.client = client
        self.settings = settings

    async def join_group(self, group_ref: str) -> Any:
        """Вступить в группу (если пригласительная ссылка/новая группа)."""
        try:
            entity = await self.client.get_entity(group_ref)
            return entity
        except Exception:
            # invite-хэш Telethon принимает через ImportChatInvite
            if group_ref.startswith("https://t.me/+") or "/joinchat/" in group_ref:
                hash_part = group_ref.rsplit("+", 1)[-1].rsplit("/", 1)[-1]
                try:
                    await self.client(  # type: ignore[operator]
                        __import__("telethon.tl.functions.channels", fromlist=["x"]
                                   ).JoinChannelRequest  # noqa: F841
                    )
                except Exception:
                    pass
            raise

    async def scan_group(
        self,
        group: Any,  # GroupEntry из config
        since_ts: int,
        limit: int | None = None,
    ) -> list[RawPost]:
        """Прочитать одну площадку: последние сообщения, фильтр спроса."""
        limit = limit or self.settings.scan_messages_limit
        posts: list[RawPost] = []
        try:
            entity = await self.client.get_entity(group.ref)
        except Exception as e:
            print(f"[scanner] {group.username}: get_entity failed — {_invite_or_username_error(e)}")
            return posts

        async for msg in self.client.iter_messages(entity, limit=limit):
            if msg.text is None:
                continue
            posted_at = int(getattr(msg, "date", 0).timestamp()) if getattr(msg, "date", None) else 0
            if posted_at and posted_at < since_ts:
                continue
            sender = getattr(msg, "sender", None)
            user_id = int(getattr(sender, "id", 0) or 0)
            if not user_id:
                continue
            if not looks_like_demand(msg.text):
                continue
            posts.append(
                RawPost(
                    group=group.username,
                    group_title=getattr(entity, "title", group.name),
                    message_id=msg.id,
                    user_id=user_id,
                    username=getattr(sender, "username", None),
                    display_name=" ".join(
                        filter(None, [
                            getattr(sender, "first_name", None),
                            getattr(sender, "last_name", None),
                        ])
                    ) or None,
                    text=msg.text,
                    posted_at=posted_at,
                    category=group.category,
                )
            )
        return posts

    async def scan_groups(
        self,
        groups: list[Any],
        since_ts: int,
        progress: Callable[[str, int], Awaitable[None]] | None = None,
    ) -> list[RawPost]:
        """Пройти все площадки; ошибка одной не валит скан."""
        all_posts: list[RawPost] = []
        for g in groups:
            try:
                posts = await self.scan_group(g, since_ts)
                all_posts.extend(posts)
                if progress:
                    await progress(g.username, len(posts))
            except Exception as e:
                print(f"[scanner] {g.username}: skipped — {_invite_or_username_error(e)}")
        return all_posts


def message_url(group: str, message_id: int, tme: str | None = None) -> str:
    """Ссылка на сообщение: t.me/<group>/<id> (или c/-сокращение)."""
    if tme:
        return tme
    return f"https://t.me/{group}/{message_id}"


def extract_city(text: str) -> str | None:
    """Грубое извлечение города: стем-словарь + «в городе X».

    Возвращает ПОЛНОЕ слово (Казань, а не «Казан»), спб/питер —
    канонично в Санкт-Петербург.
    """
    city_stems = (
        ("москва", "Москва"), ("московск", "Московская обл."),
        ("питер", "Санкт-Петербург"), ("спб", "Санкт-Петербург"),
        ("санкт-петербург", "Санкт-Петербург"), ("казан", None),
        ("екатеринбург", None), ("новосибирск", None),
        ("нижний новгород", None), ("нижнем новгород", None),
        ("ростов", None), ("самара", None), ("краснодар", None),
        ("воронеж", None), ("тула", None), ("твер", None),
        ("ярославл", None), ("калуг", None), ("балаших", None),
        ("химк", None), ("подольск", None),
    )
    low = text.lower()
    for stem, canonical in city_stems:
        m = re.search(rf"\b{re.escape(stem)}[а-яё]*", low)
        if m:
            return canonical or m.group(0).capitalize()
    m = re.search(r"(?:в\s+городе|г\.\s*)([А-ЯЁ][а-яё-]{2,})", text)
    if m:
        return m.group(1)
    return None
