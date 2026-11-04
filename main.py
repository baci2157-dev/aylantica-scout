"""Scout — FastAPI-приложение (точка входа).

Ручки: /scan, /leads, /leads/{id}/approve, /leads/{id}/send, /inbox,
/status, /healthz. Cron-цикл сканирования — встроенный asyncio-таск
(SCAN_EVERY_H) или внешний cron (Railway cron → POST /scan).

Telethon-клиент создаётся лениво (первый /scan), живёт процессом.
"""

from __future__ import annotations

import asyncio
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, Request

import config as cfg
from config import category_to_vertical
from classifier import make_classifier
from generator import Generator
from inbox import Inbox
from scanner import Scanner, extract_city, message_url
from sender import Sender
from store import Store

SETTINGS = cfg.load_settings()
STORE = Store(SETTINGS.data_dir)
CLASSIFIER = make_classifier(SETTINGS)
GENERATOR = Generator(SETTINGS)

STATE: dict[str, Any] = {
    "started_at": int(time.time()),
    "scanning": False,
    "last_scan_result": None,
    "tg_client": None,       # ленивый Telethon-клиент
    "sender": None,
    "inbox": None,
    "classifier_mode": "zai" if SETTINGS.has_zai else "heuristic",
    "push_results": [],      # ответы Aylantica на пакеты лидов
}


# ── Telethon (лениво) ─────────────────────────────────────────────

async def get_client():
    """Telethon-клиент аккаунта-разведчика (один на процесс)."""
    if STATE["tg_client"] is not None:
        return STATE["tg_client"]
    if not SETTINGS.has_telegram:
        raise HTTPException(503, "Telethon не настроен: нужны TELEGRAM_API_ID, "
                                 "TELEGRAM_API_HASH, TELEGRAM_SESSION")
    from telethon import TelegramClient

    client = TelegramClient(
        SESSION_MEM(), SETTINGS.telegram_api_id, SETTINGS.telegram_api_hash
    )
    await client.start()  # session-строка уже содержит авторизацию
    STATE["tg_client"] = client
    STATE["sender"] = Sender(client, SETTINGS, STORE)
    STATE["inbox"] = Inbox(client, STORE)
    return client


def SESSION_MEM():
    """StringSession из ENV (без файла .session)."""
    from telethon.sessions import StringSession

    return StringSession(SETTINGS.telegram_session)


# ── Оркестрация ───────────────────────────────────────────────────

async def run_scan() -> dict:
    """Скан → фильтр → классификация → черновики → store. Идемпотентен."""
    if STATE["scanning"]:
        raise HTTPException(409, "Скан уже идёт")
    STATE["scanning"] = True
    t0 = time.time()
    try:
        client = await get_client()
        scanner = Scanner(client, SETTINGS)
        since = int(time.time()) - SETTINGS.scan_window_h * 3600
        groups = list(SETTINGS.groups)

        async def _progress(group: str, n: int) -> None:
            print(f"[scan] {group}: +{n} кандидатов")

        posts = await scanner.scan_groups(groups, since, progress=_progress)
        STORE.mark_scan()

        new_leads = 0
        classified = 0
        for post in posts:
            lead_in = {
                "group": post.group,
                "group_title": post.group_title,
                "group_msg_key": post.group_msg_key,
                "message_url": message_url(post.group, post.message_id),
                "user_id": post.user_id,
                "username": post.username,
                "display_name": post.display_name,
                "text": post.text,
                "posted_at": post.posted_at,
                "category": post.category,
                "city": extract_city(post.text),
                "status": "new",
            }
            lead = STORE.add_lead(lead_in)
            if not lead:
                continue
            new_leads += 1

            verdict = await CLASSIFIER.classify(post.text, post.category)
            status = "classified" if (verdict.is_lead and verdict.score >= SETTINGS.min_score) else "rejected"
            classified += 1 if status == "classified" else 0
            STORE.update_lead(
                lead["id"],
                status=status,
                score=verdict.score,
                reason=verdict.reason,
                business_type=verdict.business_type,
                vertical=verdict.vertical,
            )
            if status == "classified":
                draft = await GENERATOR.draft({**lead, "category": post.category})
                STORE.update_lead(lead["id"], draft=draft.text, draft_via=draft.via,
                                  status="drafted")

        result = {
            "ok": True,
            "groups": len(groups),
            "candidates": len(posts),
            "new_leads": new_leads,
            "classified": classified,
            "took_sec": round(time.time() - t0, 1),
        }
        STATE["last_scan_result"] = result
        return result
    finally:
        STATE["scanning"] = False


async def push_to_aylantica(lead: dict) -> dict | None:
    """Отправить утверждённого лида в Aylantica → пакет тенанта."""
    if not (SETTINGS.aylantica_api_url and SETTINGS.aylantica_scout_secret):
        return None
    import httpx

    payload = {
        "lead_id": lead["id"],
        "category": lead.get("category"),
        # Вертикаль платформы (v2.2, Блок 2): маппинг категории Scout →
        # вертикаль тенанта (auto → autoservice, bread → food_distribution …).
        # Приоритет: сохранённое поле лида → маппинг по категории.
        "vertical": lead.get("vertical") or category_to_vertical(lead.get("category", "")),
        "business_type": lead.get("business_type"),
        "city": lead.get("city"),
        "text": lead.get("text"),
        "username": lead.get("username"),
        "score": lead.get("score"),
    }
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                f"{SETTINGS.aylantica_api_url}/api/scout/leads",
                json=payload,
                headers={"X-Scout-Secret": SETTINGS.aylantica_scout_secret},
            )
            resp.raise_for_status()
            package = resp.json()
            STATE["push_results"] = (STATE["push_results"] or [])[-20:] + [
                {"lead_id": lead["id"], "ts": int(time.time()), "package": package}
            ]
            STORE.update_lead(lead["id"], aylantica_package=package)
            return package
    except Exception as e:
        print(f"[push] Aylantica error: {type(e).__name__}: {e}")
        return None


# ── Cron-цикл ─────────────────────────────────────────────────────

async def scan_loop():
    """Встроенный планировщик: раз в SCAN_EVERY_H часов."""
    every_h = float(os.getenv("SCAN_EVERY_H", "0") or 0)
    if every_h <= 0:
        return
    print(f"[cron] авто-скан каждые {every_h} ч")
    while True:
        try:
            await run_scan()
        except Exception as e:
            print(f"[cron] scan failed: {type(e).__name__}: {e}")
        await asyncio.sleep(every_h * 3600)


@asynccontextmanager
async def lifespan(app: FastAPI):
    STORE.heartbeat()
    task = asyncio.create_task(scan_loop())
    yield
    task.cancel()
    if STATE["tg_client"] is not None:
        try:
            await STATE["tg_client"].disconnect()
        except Exception:
            pass


app = FastAPI(title="Aylantica Scout", version="1.0.0", lifespan=lifespan)


# ── Ручки ─────────────────────────────────────────────────────────

@app.get("/healthz")
async def healthz():
    return {"ok": True}


@app.get("/status")
async def status():
    STORE.heartbeat()
    meta = STORE.meta.read()
    last_hb = meta.get("last_heartbeat")
    return {
        "service": "scout",
        "online_since": datetime.fromtimestamp(
            STATE["started_at"], tz=timezone.utc).isoformat(),
        "telegram_configured": SETTINGS.has_telegram,
        "telegram_connected": STATE["tg_client"] is not None,
        "classifier": STATE["classifier_mode"],
        "groups": [
            {"username": g.username, "category": g.category, "type": g.type,
             "estimated_members": g.estimated_members}
            for g in SETTINGS.groups
        ],
        "last_scan_at": meta.get("last_scan_at"),
        "last_scan_result": STATE["last_scan_result"],
        "limits": {
            "daily_send_limit": SETTINGS.daily_send_limit,
            "send_interval_sec": SETTINGS.send_interval_sec,
            "warmup_no_link_days": SETTINGS.warmup_no_link_days,
            "group_cooldown_h": SETTINGS.group_cooldown_h,
            "user_cooldown_days": SETTINGS.user_cooldown_days,
        },
        "stats": STORE.stats(),
        "aylantica_pushes": len(STATE["push_results"] or []),
        "scanning": STATE["scanning"],
    }


@app.post("/scan")
async def scan():
    return await run_scan()


@app.get("/leads")
async def leads(status: str | None = None, category: str | None = None, limit: int = 200):
    return {"items": STORE.list_leads(status=status, category=category)[:limit]}


@app.post("/leads/{lead_id}/approve")
async def approve(lead_id: str):
    lead = STORE.get_lead(lead_id)
    if not lead:
        raise HTTPException(404, "лид не найден")
    if lead["status"] not in ("drafted", "classified", "new"):
        raise HTTPException(409, f"статус {lead['status']} не подлежит утверждению")
    package = await push_to_aylantica(lead)
    updated = STORE.update_lead(lead_id, status="approved", approved_at=int(time.time()))
    return {"lead": updated, "aylantica_package": package}


@app.post("/leads/{lead_id}/reject")
async def reject(lead_id: str):
    lead = STORE.get_lead(lead_id)
    if not lead:
        raise HTTPException(404, "лид не найден")
    updated = STORE.update_lead(lead_id, status="rejected")
    return {"lead": updated}


@app.post("/leads/{lead_id}/send")
async def send(lead_id: str):
    lead = STORE.get_lead(lead_id)
    if not lead:
        raise HTTPException(404, "лид не найден")
    if lead["status"] != "approved":
        raise HTTPException(409, f"статус {lead['status']}: сначала approve")
    await get_client()
    ok, reason = await STATE["sender"].send(lead)
    if not ok:
        raise HTTPException(429, reason)
    return {"ok": True, "reason": reason, "lead": STORE.get_lead(lead_id)}


@app.get("/inbox")
async def inbox(limit: int = 100):
    return {"items": STORE.list_inbox(limit=limit)}


@app.post("/inbox/fetch")
async def inbox_fetch():
    """Собрать ответы вручную (иначе — при каждом /scan)."""
    await get_client()
    dm = await STATE["inbox"].fetch_dm_replies()
    bot = await STATE["inbox"].fetch_bot_updates(SETTINGS.scout_bot_token)
    return {"dm_new": len(dm), "bot_new": len(bot)}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=SETTINGS.port)
