# Scout → Railway: пошаговый деплой (15 минут)

> Коротко: Railway → New Project → GitHub repo `baci2157-dev/Aylantica`,
> Root Directory `scout/` → Variables → Volume → Deploy.
> Ниже каждый шаг с картинками словами и проверками.

> **Почему Railway, а не Vercel**: Scout держит постоянное соединение
> Telethon с Telegram и фоновый цикл сканов — serverless Vercel это не
> умеет. Нужен «вечно живой» контейнер: Railway ($5/мес Hobby) или любой
> VPS (Docker, см. `scout/DEPLOY-VPS.md`).
>
> **v2.1**: приём лидов `POST /api/scout/leads` защищён серверным
> секретом `X-Scout-Secret` и не зависит от Supabase Auth — перевязка
> авторизации платформы деплой Scout не ломает.

## Что получится

- `https://<имя>.up.railway.app` — публичный URL Scout.
- `/healthz` — liveness, `/status` — статистика, `/scan` — запуск парсинга.
- Лиды прилетают в Aylantica: `POST /api/scout/leads` (пакет тенанта).

## Шаг 0. Что понадобится (списком)

| Что | Где взять | Пример |
|---|---|---|
| Аккаунт Railway | [railway.app](https://railway.app) → Login with GitHub | — |
| Репозиторий | уже запушен: `baci2157-dev/Aylantica`, ветка `main` | — |
| `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` | [my.telegram.org](https://my.telegram.org) → API development tools → приложение `teleqatradelink` | число / строка |
| `TELEGRAM_SESSION` | см. Шаг 1 ниже (одноразовый запуск локально) | длинная строка |
| `ZAI_API_KEY` | ключ Z.AI (GLM) | `09fd…` |
| `AYLANTICA_API_URL` | URL деплоя платформы | `https://aylantica-autoservice.vercel.app` |
| `AYLANTICA_SCOUT_SECRET` | любая длинная случайная строка (придумайте) | `openssl rand -hex 24` |
| `SCOUT_BOT_TOKEN` | токен @aylatica_sales_bot от @BotFather | `894…` |
| `OWNER_TELEGRAM_ID` | ваш id (узнал @userinfobot) | число |

## Шаг 1. TELEGRAM_SESSION (одноразово, 2 минуты)

Нужен Python 3.10+ локально:

```bash
git clone https://github.com/baci2157-dev/Aylantica.git
cd Aylantica/scout
python3 -m venv .venv && source .venv/bin/activate
pip install telethon

TELEGRAM_API_ID=<ваш api_id> \
TELEGRAM_API_HASH=<ваш api_hash> \
python -m scout.session_login
```

(если запускаете из корня репо: `python -m scout.session_login`
при `cd scout` — `python session_login.py`)

1. Введите телефон аккаунта-разведчика (`+7…`).
2. Введите код из Telegram.
3. В терминале появится **SESSION-строка** (~200 символов) —
   скопируйте: это `TELEGRAM_SESSION`.

**Важно:** аккаунт-разведчик должен вступить в закрытые чаты из
`scout/groups.json` руками с телефона (публичные Scout вступит сам).
Первые 2 дня (`WARMUP_NO_LINK_DAYS`) Scout шлёт касания без ссылок —
прогрев против бана.

## Шаг 2. Railway-проект (5 минут)

1. [railway.app](https://railway.app) → **Login with GitHub**
   (разрешите доступ к `baci2157-dev/Aylantica`).
2. **New Project → Deploy from GitHub repo → Aylantica**.
3. В карточке сервиса → **Settings**:
   - **Root Directory**: `scout` ← обязательно (иначе соберёт Next.js).
   - Railway увидит `scout/Dockerfile` и соберёт образ сам.
   - **Service name**: `aylantica-scout`.
4. Дождитесь статуса **Building → Deployed** (2-4 минуты).
5. **Networking → Generate Domain** → получите
   `https://aylantica-scout-production.up.railway.app` (пример).
   Это и есть `SCOUT_PUBLIC_URL` для платформы.

## Шаг 3. Variables (Service → Variables)

Вставьте по одной (или Bulk Import — текст ниже):

```
TELEGRAM_API_ID=
TELEGRAM_API_HASH=
TELEGRAM_SESSION=
ZAI_API_KEY=
AYLANTICA_API_URL=https://aylantica-autoservice.vercel.app
AYLANTICA_SCOUT_SECRET=<та же строка, что в SCOUT_SECRET платформы>
SCOUT_BOT_TOKEN=
OWNER_TELEGRAM_ID=
SCAN_EVERY_H=4
DATA_DIR=/data
PORT=8080
```

Каждое добавление переменной перезапускает сервис — это нормально.

## Шаг 4. Volume (обязательно!)

Без тома лиды живут в памяти пода и пропадают при каждом рестарте.

**Settings → Volumes → New Volume**:
- **Mount path**: `/data`

(переменная `DATA_DIR=/data` уже указывает туда Scout).

## Шаг 5. Проверка (2 минуты)

```bash
# 1) живой?
curl https://<scout-url>/healthz
# → {"ok":true}

# 2) статус: группы, лимиты, классификатор
curl https://<scout-url>/status
# → "groups": [...14 площадок...], "classifier": "zai"

# 3) первый скан (Telethon-сессия + чтение групп, до минуты)
curl -X POST https://<scout-url>/scan
# → {"ok":true,"groups":14,"candidates":N,"new_leads":N,...}

# 4) лиды после классификации
curl "https://<scout-url>/leads?status=drafted"
```

Дальше — human-in-the-loop:

```bash
curl -X POST https://<scout-url>/leads/<ID>/approve   # + пакет в Aylantica
curl -X POST https://<scout-url>/leads/<ID>/send      # касание автору
curl https://<scout-url>/inbox                        # ответы
```

## Шаг 6. Связать с Aylantica (Vercel)

В проект-тенант на Vercel (например `aylantica-autoservice`) добавьте ENV:

```
SCOUT_SECRET=<та же строка, что AYLANTICA_SCOUT_SECRET у Scout>
SCOUT_PUBLIC_URL=https://<scout-url>
```

После этого в **/settings → блок Scout** появится зелёная точка «Online»
и живые цифры, а `POST /api/scout/leads` начнёт принимать пакеты.

## Шаг 7. E2E: Scout находит лида → лид в Aylantica (5 минут)

Полная цепочка одним прогоном — так мы проверяли интеграцию в песочнице:

```bash
# 1) Scout жив
curl https://<scout-url>/healthz
# → {"ok":true}

# 2) скан групп → кандидаты → черновики лидов
#    (Telethon-сессия + чтение 14 площадок, до минуты)
curl -X POST https://<scout-url>/scan
# → {"ok":true,"groups":14,"candidates":N,"new_leads":N}

# 3) approve лида → Scout POST-ит пакет в Aylantica
curl -X POST https://<scout-url>/leads/<ID>/approve
# → в логах Scout: POST /api/scout/leads → 201
curl "https://<scout-url>/leads?status=approved"

# 4) подтверждение на стороне платформы
#    (из песочницы, с тем же секретом):
curl -H "X-Scout-Secret: <SCOUT_SECRET>" \
     https://aylantica-autoservice.vercel.app/api/scout/leads
# → список лидов с нашим пакетом
curl https://aylantica-autoservice.vercel.app/api/scout/status
# → {"scout":{"online":true,...},"local":{...}}
```

И в UI: **Настройки → Разведчик (Scout)** — зелёный индикатор, счётчики
лидов и последние находки. Лид попадает в админку тенанта сразу после
approve (файловое хранилище роута — персистентно на инстансе Vercel).

Если п.3 падает с 401 — секреты не совпадают (`AYLANTICA_SCOUT_SECRET`
↔ `SCOUT_SECRET`), см. таблицу ниже.

## Cron (по желанию)

Встроенный цикл уже работает (`SCAN_EVERY_H=4`). Если хочется
внешний cron: Railway → ваш проект → **Cron** →
`curl -X POST https://<scout-url>/scan` каждые 4 часа
(и поставьте `SCAN_EVERY_H=0`).

## Частые проблемы

| Симптом | Причина | Лечение |
|---|---|---|
| `/scan` → 503 «Telethon не настроен» | нет `TELEGRAM_SESSION` | Шаг 1 + Variables |
| `AuthKeyUnregistered` в логах | сессия умерла (выход из аккаунта) | перевыпустить SESSION (Шаг 1) |
| `FloodWaitError` | Telegram rate-limit | поднять `SEND_INTERVAL_SEC` до 3600 |
| Лидов 0 после скана | группы «молчат» или фильтр строг | `SCAN_WINDOW_H=24`, `MIN_SCORE=5` |
| Aylantica 401 на пакете | секреты не совпадают | сверить `AYLANTICA_SCOUT_SECRET` ↔ `SCOUT_SECRET` |
| Deploy падает на Dockerfile | Root Directory не `scout` | Settings → Root Directory = `scout` |

## Альтернативы

- **VPS (Timeweb / любой хостинг с Docker)** — готовая команда одной
  строкой в `scout/DEPLOY-VPS.md` (~300 ₽/мес вместо $5).
- **Fly.io** — см. `scout/DEPLOY.md` (там готовые команды).
