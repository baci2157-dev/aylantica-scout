"""JSON-хранилище Scout: лиды, инбокс, лог отправок.

Один процесс → файла с атомарной записью (tmp + rename) достаточно.
Форматы фиксированы версиями в поле "v".
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

LOCK = threading.RLock()

LEAD_STATUSES = (
    "new",          # найден сканером
    "classified",   # прошёл классификацию ZAI/эвристикой
    "drafted",      # есть черновик касания
    "approved",     # владелец утвердил отправку (+ пак в Aylantica)
    "sent",         # отправлено автору
    "replied",      # автор ответил
    "rejected",     # владелец отклонил
)


def now() -> int:
    return int(time.time())


def new_id() -> str:
    return uuid.uuid4().hex[:12]


class JsonFile:
    """Тонкая обёртка над JSON-файлом с атомарной записью."""

    def __init__(self, path: Path, default: dict[str, Any]):
        self.path = path
        self.default = default
        self._cache: dict[str, Any] | None = None

    def read(self) -> dict[str, Any]:
        if self._cache is not None:
            return self._cache
        if not self.path.exists():
            self._cache = dict(self.default)
            return self._cache
        try:
            self._cache = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            self._cache = dict(self.default)
        return self._cache

    def write(self, data: dict[str, Any]) -> None:
        with LOCK:
            self._cache = data
            tmp = self.path.with_suffix(".tmp")
            tmp.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            tmp.replace(self.path)

    def mutate(self, fn) -> dict[str, Any]:
        """Атомарно: прочитать → изменить → записать."""
        with LOCK:
            data = self.read()
            fn(data)
            self.write(data)
            return data


class Store:
    """Хранилище Scout: leads / inbox / sent_log.

    clock — инъекция времени (тесты подставляют детерминированное).
    """

    def __init__(self, data_dir: str | Path, clock: Callable[[], float] = time.time):
        d = Path(data_dir)
        d.mkdir(parents=True, exist_ok=True)
        self._clock = clock
        self.leads = JsonFile(d / "leads.json", {"v": 1, "items": []})
        self.inbox = JsonFile(d / "inbox.json", {"v": 1, "items": []})
        self.sent_log = JsonFile(d / "sent_log.json", {"v": 1, "items": []})
        self.meta = JsonFile(
            d / "meta.json",
            {"v": 1, "created_at": self.now(), "account_first_seen": None,
             "last_scan_at": None, "last_heartbeat": None},
        )

    def now(self) -> int:
        return int(self._clock())

    # ── Лиды ─────────────────────────────────────────────────────

    def add_lead(self, lead: dict[str, Any]) -> dict[str, Any]:
        """Создать лида (дедуп по group_msg_key). Вернуть созданного или None."""
        with LOCK:
            data = self.leads.read()
            if any(i.get("group_msg_key") == lead["group_msg_key"] for i in data["items"]):
                return {}
            item = {
                "id": new_id(),
                "status": lead.get("status", "new"),
                "created_at": self.now(),
                **lead,
            }
            data["items"].append(item)
            self.leads.write(data)
            return item

    def get_lead(self, lead_id: str) -> dict[str, Any] | None:
        return next(
            (i for i in self.leads.read()["items"] if i["id"] == lead_id), None
        )

    def update_lead(self, lead_id: str, **fields: Any) -> dict[str, Any] | None:
        with LOCK:
            data = self.leads.read()
            for i in data["items"]:
                if i["id"] == lead_id:
                    i.update(fields)
                    self.leads.write(data)
                    return i
            return None

    def list_leads(
        self, status: str | None = None, category: str | None = None
    ) -> list[dict[str, Any]]:
        items = self.leads.read()["items"]
        out = items
        if status:
            out = [i for i in out if i.get("status") == status]
        if category:
            out = [i for i in out if i.get("category") == category]
        return list(reversed(out))  # свежие сверху

    # ── Инбокс ───────────────────────────────────────────────────

    def add_inbox_item(self, item: dict[str, Any]) -> dict[str, Any]:
        with LOCK:
            data = self.inbox.read()
            if any(
                i.get("message_id") == item.get("message_id")
                and i.get("source") == item.get("source")
                for i in data["items"]
            ):
                return {}
            # если лид не указан явно — матчим по автору (как Inbox)
            lead_id = item.get("lead_id")
            if not lead_id and item.get("user_id"):
                match = self.lead_id_for_user(int(item["user_id"]))
                lead_id = match["id"] if match else None
            entry = {"id": new_id(), "received_at": self.now(),
                     "lead_id": lead_id, **item}
            data["items"].append(entry)
            self.inbox.write(data)
            # лид, которому ответили, помечаем
            if entry.get("lead_id"):
                self.update_lead(entry["lead_id"], status="replied")
            return entry

    def list_inbox(self, limit: int = 100) -> list[dict[str, Any]]:
        return list(reversed(self.inbox.read()["items"]))[:limit]

    # ── Лог отправок ─────────────────────────────────────────────

    def log_send(self, lead_id: str, user_id: int, group: str) -> None:
        with LOCK:
            data = self.sent_log.read()
            data["items"].append(
                {"ts": self.now(), "lead_id": lead_id, "user_id": user_id, "group": group}
            )
            self.sent_log.write(data)

    def sends(self) -> list[dict[str, Any]]:
        return self.sent_log.read()["items"]

    def sends_today(self, ts: int | None = None) -> list[dict[str, Any]]:
        """Отправки за текущие календарные сутки (локальная таймзона)."""
        ts = ts if ts is not None else self.now()
        day_start = ts - (ts % 86400)
        return [s for s in self.sends() if s["ts"] >= day_start]

    def last_send_ts(self) -> int | None:
        items = self.sends()
        return max((s["ts"] for s in items), default=None)

    def last_send_to_group(self, group: str) -> int | None:
        items = [s for s in self.sends() if s["group"] == group]
        return max((s["ts"] for s in items), default=None)

    def last_send_to_user(self, user_id: int) -> int | None:
        items = [s for s in self.sends() if s["user_id"] == user_id]
        return max((s["ts"] for s in items), default=None)

    # ── Мета (heartbeat, статистика) ─────────────────────────────

    def heartbeat(self) -> None:
        self.meta.mutate(lambda d: d.update({"last_heartbeat": self.now()}))

    def mark_scan(self) -> None:
        self.meta.mutate(lambda d: d.update({"last_scan_at": self.now()}))

    def ensure_account_first_seen(self) -> int:
        """Первое наблюдение аккаунта — точка отсчёта прогрева."""
        with LOCK:
            data = self.meta.read()
            if not data.get("account_first_seen"):
                data["account_first_seen"] = self.now()
                self.meta.write(data)
            return data["account_first_seen"]

    def lead_id_for_user(self, user_id: int) -> dict[str, Any] | None:
        """Последний отправленный/ожидающий лид этого пользователя."""
        candidates = [
            i for i in self.list_leads()
            if int(i.get("user_id", 0)) == user_id
            and i.get("status") in ("sent", "approved", "replied")
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda i: i.get("sent_at") or i.get("created_at", 0))

    def stats(self) -> dict[str, Any]:
        items = self.leads.read()["items"]
        by_status: dict[str, int] = {}
        for i in items:
            by_status[i.get("status", "?")] = by_status.get(i.get("status", "?"), 0) + 1
        return {
            "leads_total": len(items),
            "by_status": by_status,
            "sends_total": len(self.sends()),
            "sends_today": len(self.sends_today()),
            "inbox_total": len(self.inbox.read()["items"]),
        }
