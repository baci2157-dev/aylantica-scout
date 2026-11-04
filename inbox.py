"""Инбокс: сбор ответов на касания.

Два источника:
  1. DM аккаунта-разведчика (Telethon): читаем входящие диалоги,
     матчим по user_id → лиду;
  2. Bot API getUpdates (@aylatica_sales_bot): люди, написавшие боту
     напрямую (в т.ч. новые, без лида).

Дедупликация: (source, message_id).
"""

from __future__ import annotations

from typing import Any

from store import Store


class Inbox:
    def __init__(self, client: Any, store: Store):
        self.client = client
        self.store = store

    async def fetch_dm_replies(self, limit_per_dialog: int = 5) -> list[dict]:
        """Входящие сообщения в личке разведчика → store.inbox.

        client.iter_dialogs() — как у Telethon. Личка определяется
        как диалог с user=True и не чат-группа.
        """
        found: list[dict] = []
        try:
            async for dialog in self.client.iter_dialogs():
                if not dialog.is_user:
                    continue
                user_id = int(dialog.id)
                async for msg in self.client.iter_messages(
                    dialog.entity, limit=limit_per_dialog
                ):
                    if msg.out or not msg.text:
                        continue
                    lead = self._lead_for_user(user_id)
                    item = {
                        "source": "dm",
                        "message_id": int(msg.id),
                        "user_id": user_id,
                        "username": getattr(getattr(msg, "sender", None), "username", None),
                        "text": msg.text,
                        "lead_id": lead["id"] if lead else None,
                        "lead_status": lead.get("status") if lead else None,
                    }
                    if item["lead_id"] or self._is_reply_like(msg.text):
                        saved = self.store.add_inbox_item(item)
                        if saved:
                            found.append(saved)
        except Exception as e:
            print(f"[inbox] dm fetch error: {type(e).__name__}: {e}")
        return found

    def _lead_for_user(self, user_id: int) -> dict | None:
        """Последний отправленный/ожидающий лид этого пользователя."""
        return self.store.lead_id_for_user(user_id)

    @staticmethod
    def _is_reply_like(text: str) -> bool:
        low = (text or "").lower()
        return any(
            kw in low
            for kw in ("да", "интересно", "скинь", "пришлите", "цены", "прайс",
                       "актуально", "давайте", "здравствуйте", "добрый")
        )

    async def fetch_bot_updates(self, bot_token: str, timeout: int = 0) -> list[dict]:
        """Сообщения, написанные боту напрямую (long-polling Bot API)."""
        if not bot_token:
            return []
        import httpx

        found: list[dict] = []
        try:
            async with httpx.AsyncClient(timeout=25) as client:
                resp = await client.get(
                    f"https://api.telegram.org/bot{bot_token}/getUpdates",
                    params={"timeout": timeout, "allowed_updates": '["message"]'},
                )
                resp.raise_for_status()
                for upd in resp.json().get("result", []):
                    msg = upd.get("message") or {}
                    if not msg.get("text"):
                        continue
                    user = msg.get("from", {})
                    chat_id = msg.get("chat", {}).get("id")
                    if not chat_id:
                        continue
                    item = {
                        "source": "bot",
                        "message_id": int(upd.get("update_id", 0)),
                        "user_id": int(user.get("id", 0) or 0),
                        "username": user.get("username"),
                        "text": msg.get("text", ""),
                        "lead_id": None,
                        "lead_status": None,
                    }
                    saved = self.store.add_inbox_item(item)
                    if saved:
                        found.append(saved)
        except Exception as e:
            print(f"[inbox] bot fetch error: {type(e).__name__}: {e}")
        return found
