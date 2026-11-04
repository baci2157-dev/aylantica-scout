# Scout — деплой

Scout — обычный долгоживущий Docker-сервис. Рекомендация: **Railway**
(просто, есть Volume, есть cron). Альтернативы: Fly.io, любой VPS.

## 0. Подготовка (одноразово)

1. `my.telegram.org` → API development tools → запишите `api_id` / `api_hash`.
2. Локально из каталога `scout/`:
   ```bash
   python3 -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   TELEGRAM_API_ID=… TELEGRAM_API_HASH=… python -m scout.session_login
   ```
   Введите телефон аккаунта-разведчика и код → получите `TELEGRAM_SESSION`.
3. С телефона этого аккаунта желательно вступить в закрытые чаты из
   `groups.json` (публичные Scout вступит сам при первом скане).

## 1. Railway (рекомендую)

1. [railway.app](https://railway.app) → **New Project** → **Deploy from GitHub repo**
   (репозиторий Aylantica, **Root Directory = `scout/`**) — Railway увидит
   `Dockerfile` и соберёт образ сам.
2. **Variables** (Services → Scout → Variables):
   ```
   TELEGRAM_API_ID=…
   TELEGRAM_API_HASH=…
   TELEGRAM_SESSION=…        # строка из session_login
   ZAI_API_KEY=…
   AYLANTICA_API_URL=https://<ваш-деплой-платформы>
   AYLANTICA_SCOUT_SECRET=<длинная случайная строка>
   SCOUT_BOT_TOKEN=…         # @aylatica_sales_bot
   OWNER_TELEGRAM_ID=…
   SCAN_EVERY_H=4
   DATA_DIR=/data
   ```
3. **Settings → Volumes**: примонтируйте том в `/` → путь `/data`
   (иначе лиды живут только в памяти пода). Переменная `DATA_DIR=/data`.
4. **Networking → Generate Domain** — публичный URL Scout
   (его же укажите в платформе ENV `SCOUT_PUBLIC_URL` для /settings).
5. Проверка:
   ```bash
   curl https://<scout>.up.railway.app/healthz
   curl -X POST https://<scout>.up.railway.app/scan
   curl https://<scot>.up.railway.app/leads?status=drafted
   ```
6. Cron (по желанию вместо встроенного): Railway → **Cron** (или
   github-actions c curl): `POST /scan` каждые 4 часа; `SCAN_EVERY_H=0`.

## 2. Fly.io

```bash
cd scout
fly launch --dockerfile Dockerfile --name aylantica-scout --region fra
fly volumes create scout_data --size 1          # том для /data
fly secrets set TELEGRAM_API_ID=… TELEGRAM_API_HASH=… TELEGRAM_SESSION=… \
                  ZAI_API_KEY=… AYLANTICA_API_URL=… AYLANTICA_SCOUT_SECRET=… \
                  SCOUT_BOT_TOKEN=… OWNER_TELEGRAM_ID=… DATA_DIR=/data
fly deploy
```

В `fly.toml` добавьте:
```toml
[[mounts]]
  source = "scout_data"
  destination = "/data"

[checks.health]
  port = 8080
  type = "http"
  path = "/healthz"
```

## 3. VPS (systemd + Docker)

```bash
git clone https://github.com/baci2157-dev/fryflow-platform.git aylantica
cd aylantica/scout

cat > .env <<EOF
TELEGRAM_API_ID=…
TELEGRAM_API_HASH=…
TELEGRAM_SESSION=…
ZAI_API_KEY=…
AYLANTICA_API_URL=…
AYLANTICA_SCOUT_SECRET=…
SCOUT_BOT_TOKEN=…
OWNER_TELEGRAM_ID=…
EOF

docker compose up -d --build
```

`docker-compose.yml`:
```yaml
services:
  scout:
    build: .
    restart: unless-stopped
    ports: ["8080:8080"]
    env_file: .env
    volumes: ["./data:/app/data"]
```

## 4. После деплоя — связать с Aylantica

На стороне платформы (Vercel → проект-тенант → ENV):
```
SCOUT_SECRET=<та же AYLANTICA_SCOUT_SECRET>
SCOUT_PUBLIC_URL=https://<scout>.up.railway.app
```
Тогда `/settings` покажет статус Scout (heartbeat `/status`), а
`POST /api/scout/leads` начнёт принимать пакеты.

## Частые проблемы

| Симптом | Причина | Лечение |
|---|---|---|
| `/scan` → 503 | нет `TELEGRAM_SESSION` | прогнать `session_login`, задать ENV |
| Telethon `AuthKeyUnregistered` | сессия умерла (выход из аккаунта) | перевыпустить `TELEGRAM_SESSION` |
| `FloodWaitError` в логах | Telegram rate-limit | поднять `SEND_INTERVAL_SEC`, снизить `DAILY_SEND_LIMIT` |
| Лиды не приходят | группы «молчат»/фильтр строг | поднять `SCAN_WINDOW_H`, снизить `MIN_SCORE` до 5 |
| Aylantica 401 | разные секреты | сверить `AYLANTICA_SCOUT_SECRET` и `SCOUT_SECRET` |
