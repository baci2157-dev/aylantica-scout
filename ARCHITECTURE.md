# Scout — архитектура

## Схема

```
                        ┌─────────────────────────────────────────────────┐
                        │                 Telegram (Telethon)             │
                        │  группы/каналы 6 вертикалей (groups.json)       │
                        └───────────────────────┬─────────────────────────┘
                                                │ iter_messages (последние N часов)
                                                ▼
   ┌─────────┐    raw posts    ┌──────────────┐   {is_lead, score, reason, biz}
   │ scanner │ ──────────────► │  classifier  │ ─────────────┐
   └─────────┘                 └──────────────┘               │
      Telethon                 ZAI GLM (glm-4.6)              ▼
      фильтр ключевых          fallback: эвристика      ┌──────────┐
      слов + антишум             (офлайн, тесты)        │  store   │
                                                      │ leads.json
                                                      └────┬─────┘
                                                           │ GET /leads
                                                           ▼
                          владелец платформы (human-in-the-loop)
                                                           │ approve
                                                           ▼
   ┌──────────┐   draft    ┌──────────────┐   send     ┌──────────┐
   │ generator│ ◄──────────│    lead      │ ─────────► │  sender  │
   └──────────┘            └──────────────┘            └──────────┘
   шаблон вертикали                                     Telethon DM
   + LLM-полировка                                      лимиты ↓
   (опционально)                                        прогрев, cooldown
                                                           │
                                            ┌──────────────┴───────────┐
                                            ▼                          ▼
                                     ┌────────────┐            ┌────────────┐
                                     │   inbox    │            │  Aylantica │
                                     │ inbox.json │            │  Next.js   │
                                     └────────────┘            └────────────┘
                                     ответы в DM         POST /api/scout/leads
                                     + Bot API           (пакет тенанта, дашборд)
```

## Потоки данных

### 1. Скан (`POST /scan` или cron)

1. `scanner.scan_groups(client, groups, since)` — по каждой площадке:
   вступление (если ещё не в чате), чтение последних сообщений за
   `SCAN_WINDOW_H` (по умолчанию 12 ч).
2. Грубый фильтр: ключевые слова вертикали («нужен», «ищу», «поставщик»,
   «опт», «куплю», «посоветуйте»…) + антишум (вакансии, «продам» — мимо).
3. Дедупликация: `(group_id, message_id)` уже в `leads.json` → пропуск.

### 2. Классификация

- `classifier.classify(text, category)` — запрос к ZAI API
  (`POST /api/paas/v4/chat/completions`, модель `glm-4.6`, JSON-режим):
  `{is_lead, score 0-10, reason, business_type}`.
- Fallback без ключа/сети — эвристика по ключевым словам
  (`classifier.HeuristicClassifier`): тот же контракт, качество ниже,
  зато офлайн (тесты, локальная разработка).
- Лид = `is_lead && score >= MIN_SCORE` (по умолчанию 6).

### 3. Генерация черновика

- `generator.draft(lead)` — шаблон вертикали с плейсхолдерами
  (имя, бизнес, город из текста) — детерминированный, безопасный.
- Если `ZAI_API_KEY` задан — LLM переписывает черновик «по-человечески»
  (коротко, без канцелярита, без ссылок в прогрев).
- Статус лида: `new → classified → drafted`.

### 4. Утверждение и отправка (human-in-the-loop)

- `POST /leads/{id}/approve` — владелец читает черновик, жмёт approve.
  Статус: `drafted → approved`. Утверждённый лид пушится в Aylantica
  `POST /api/scout/leads` (секрет в заголовке `X-Scout-Secret`) —
  платформа возвращает пакет тенанта (сегмент, промпты, КП).
- `POST /leads/{id}/send` — отправка через Telethon-аккаунт разведчика.
  Гейт в `sender.py`:
  - дневной лимит `DAILY_SEND_LIMIT`;
  - интервал `SEND_INTERVAL_SEC` от последней отправки;
  - прогрев: первые `WARMUP_NO_LINK_DAYS` дней аккаунта — черновик без ссылок;
  - кулдаун группы `GROUP_COOLDOWN_H`: не дёргать одну группу чаще раза в N часов;
  - уже отправляли этому пользователю за `USER_COOLDOWN_DAYS` — отказ.
  Каждая отправка — запись в `data/sent_log.json` (ts, lead_id, user, group).

### 5. Инбокс

- `inbox.fetch_replies(client)` — входящие DM аккаунта-разведчика,
  матч по `user_id` → лид получает `replied` и текст ответа.
- Bot API (`SCOUT_BOT_TOKEN`, getUpdates): сообщения, которые люди пишут
  боту @aylatica_sales_bot напрямую (попадают в тот же `inbox.json`,
  поле `source: dm|bot`).

## Хранилище

JSON-файлы в `data/` (том Docker / Railway-диск): `leads.json`,
`inbox.json`, `sent_log.json`. Атомарная запись (tmp+rename), блокировка
на запись — один процесс, этого достаточно. Состояния лида:

```
new → classified → drafted → approved → sent → replied
                                     ↘ rejected
```

## ENV

См. `.env.example`. Ключевые: `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`,
`TELEGRAM_SESSION`, `ZAI_API_KEY`, `ZAI_MODEL`, `AYLANTICA_API_URL`,
`AYLANTICA_SCOUT_SECRET`, `SCOUT_BOT_TOKEN`, `OWNER_TELEGRAM_ID`,
`SCAN_CRON` (default `0 */4 * * *` — каждые 4 часа), лимиты отправки.

## Почему отдельный сервис

- Telethon требует долгоживящего процесса и персистентной сессии —
  в serverless (Vercel) он не живёт.
- Отдельный масштабируемый контур: Scout можно остановить/обновить,
  не трогая CRM-платформу.
- Изоляция секретов: ключи Telegram-аккаунта живут только в Scout.

## Интеграция с Aylantica (сторона Next.js)

- `POST /api/scout/leads` — приём лида (secret-заголовок), создание
  «тенанта-пакета»: рекомендации сегмента, промптов, КП. Пишет в
  `data/<prefix>-scout.json`.
- `GET /api/scout/status` — статус Scout для UI (/settings → блок Scout):
  online (heartbeat), найдено/отправлено лидов.
- `/settings` — блок «Scout»: статус, групп сканируется, ключевые слова,
  ссылка на дашборд Scout (SCOUT_PUBLIC_URL).
