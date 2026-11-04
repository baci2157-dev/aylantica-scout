"""Одноразовый скрипт: получить TELEGRAM_SESSION (StringSession).

Запуск:  python -m scout.session_login   (из каталога scout/)
Вводите телефон аккаунта-разведчика и код из Telegram — в stdout
печатается строка сессии. Её — в ENV TELEGRAM_SESSION (Railway).
"""

import asyncio
import os

from telethon import TelegramClient
from telethon.sessions import StringSession


async def main() -> None:
    api_id = int(os.getenv("TELEGRAM_API_ID", "0"))
    api_hash = os.getenv("TELEGRAM_API_HASH", "")
    if not (api_id and api_hash):
        raise SystemExit("Сначала задайте TELEGRAM_API_ID и TELEGRAM_API_HASH (из my.telegram.org)")

    print("Вход в аккаунт-разведчик. Номер телефона в формате +79991234567:")
    async with TelegramClient(StringSession(), api_id, api_hash) as client:
        session_string = client.session.save()
        me = await client.get_me()
        print("\n" + "=" * 60)
        print(f"Аккаунт: {me.first_name} (@{me.username})")
        print("SESSION-строка (положите в ENV TELEGRAM_SESSION):")
        print(session_string)
        print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
