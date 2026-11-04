"""Отправщик касаний с лимитами, прогревом и кулдаунами.

Отправка идёт с ТЕЛЕФОННОГО аккаунта разведчика (Telethon DM):
Bot API не может писать первым. Каждое касание проходит гейт
`can_send` — все лимиты в одном месте, легко тестируются.

Гейт (по порядку):
  1. статус лида approved;
  2. дневной лимит DAILY_SEND_LIMIT;
  3. интервал SEND_INTERVAL_SEC от последней отправки;
  4. кулдаун группы GROUP_COOLDOWN_H;
  5. кулдаун пользователя USER_COOLDOWN_DAYS (не дёргать дважды);
  6. прогрев: в первые WARMUP_NO_LINK_DAYS ссылки вырезаются.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

from config import Settings
from store import Store


@dataclass
class SendDecision:
    allowed: bool
    reason: str


LINK_RE = re.compile(r"https?://\S+|\b[a-z0-9.-]+\.(?:ru|com|net|org|ai|io)\S*", re.I)


def strip_links_for_warmup(text: str) -> tuple[str, bool]:
    """Убрать ссылки (прогрев). Вернуть (текст, было ли ссылок)."""
    had = bool(LINK_RE.search(text))
    return LINK_RE.sub("", text).replace("  ", " ").strip(), had


class Sender:
    """Отправщик. client — Telethon-подобный объект с send_message."""

    def __init__(self, client, settings: Settings, store: Store, clock=time.time):
        self.client = client
        self.settings = settings
        self.store = store
        self._clock = clock  # injectable для тестов

    # ── Гейт лимитов ─────────────────────────────────────────────

    def can_send(self, lead: dict) -> SendDecision:
        ts = int(self._clock())

        if lead.get("status") != "approved":
            return SendDecision(False, f"статус {lead.get('status')}, нужен approved")

        sends_today = self.store.sends_today(ts)
        if len(sends_today) >= self.settings.daily_send_limit:
            return SendDecision(
                False,
                f"дневной лимит {self.settings.daily_send_limit} исчерпан",
            )

        last = self.store.last_send_ts()
        if last and ts - last < self.settings.send_interval_sec:
            wait_min = (self.settings.send_interval_sec - (ts - last)) // 60
            return SendDecision(False, f"интервал: ждать ещё ~{max(wait_min, 1)} мин")

        group_last = self.store.last_send_to_group(lead.get("group", ""))
        if group_last:
            cooldown_sec = int(self.settings.group_cooldown_h * 3600)
            if ts - group_last < cooldown_sec:
                left_h = (cooldown_sec - (ts - group_last)) / 3600
                return SendDecision(False, f"кулдаун группы {lead.get('group')}: {left_h:.1f} ч")

        user_last = self.store.last_send_to_user(int(lead.get("user_id", 0)))
        if user_last:
            cooldown_sec = self.settings.user_cooldown_days * 86400
            if ts - user_last < cooldown_sec:
                left_d = (cooldown_sec - (ts - user_last)) / 86400
                return SendDecision(False, f"этому человеку уже писали: {left_d:.1f} дн")

        return SendDecision(True, "ok")

    # ── Отправка ─────────────────────────────────────────────────

    async def send(self, lead: dict) -> tuple[bool, str]:
        """Отправить черновик лида автору сообщения. (bool, причина)."""
        decision = self.can_send(lead)
        if not decision.allowed:
            return False, decision.reason

        text = lead.get("draft") or ""
        if not text:
            return False, "нет черновика"

        # прогрев: свежий аккаунт пишет без ссылок
        first_seen = self.store.ensure_account_first_seen()
        account_age_days = (int(self._clock()) - first_seen) / 86400
        if account_age_days < self.settings.warmup_no_link_days:
            text, _had = strip_links_for_warmup(text)

        user_ref = lead.get("username") or int(lead.get("user_id", 0))
        try:
            await self.client.send_message(user_ref, text)
        except Exception as e:
            return False, f"telegram: {type(e).__name__}: {e}"

        self.store.log_send(lead["id"], int(lead.get("user_id", 0)), lead.get("group", ""))
        self.store.update_lead(lead["id"], status="sent", sent_at=int(self._clock()))
        return True, "отправлено"

    # ── Уведомление владельцу (Bot API, если задан токен) ────────

    async def notify_owner(self, text: str) -> bool:
        """Дайджест владельцу через бота @aylatica_sales_bot (Bot API)."""
        if not (self.settings.scout_bot_token and self.settings.owner_telegram_id):
            return False
        import httpx

        url = f"https://api.telegram.org/bot{self.settings.scout_bot_token}/sendMessage"
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(url, json={
                    "chat_id": self.settings.owner_telegram_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                })
                return resp.status_code == 200
        except Exception:
            return False
