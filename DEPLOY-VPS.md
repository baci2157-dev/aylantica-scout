# Scout → VPS (Timeweb или любой хостинг с Docker)

> Альтернатива Railway из `DEPLOY-RAILWAY.md`: свой VPS (~300 ₽/мес за
> минимальную машину) и Docker. Вся подготовка — один файл ENV и одна
> команда `docker run`. Порядок шагов 0-1 (SESSION, секреты) — тот же,
> что в Railway-инструкции, здесь только транспорт другой.

## Что понадобится

- VPS с Ubuntu 22.04+ (Timeweb Cloud / RuVDS / любой) — 1 vCPU, 1 ГБ RAM хватает.
- Docker на нём: `curl -fsSL https://get.docker.com | sh` (Timeweb ставит
  из панели сам — отметьте «Docker» при создании).
- Значения ENV из `DEPLOY-RAILWAY.md` (Шаг 0 и Шаг 1): `TELEGRAM_API_ID`,
  `TELEGRAM_API_HASH`, `TELEGRAM_SESSION`, `ZAI_API_KEY`,
  `AYLANTICA_SCOUT_SECRET`, `SCOUT_BOT_TOKEN`, `OWNER_TELEGRAM_ID`.

## 1. Файл ENV на сервере (2 минуты)

```bash
ssh root@<IP-сервера>
mkdir -p /opt/scout && cd /opt/scout
nano scout.env   # вставьте и заполните:
```

```dotenv
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

`chmod 600 scout.env` — в файле сессия Telegram и ключи, лишним глазам
он не нужен.

## 2. Запуск одной командой (3 минуты)

```bash
docker run -d --name scout --restart unless-stopped \
  --env-file /opt/scout/scout.env \
  -p 8080:8080 \
  -v /opt/scout/data:/data \
  ghcr.io/baci2157-dev/aylantica-scout:latest
```

> Образ ещё не опубликован в реестре? Соберите прямо на сервере из репо
> (2-4 минуты):
> ```bash
> git clone https://github.com/baci2157-dev/Aylantica.git /opt/Aylantica
> docker build -t aylantica-scout /opt/Aylantica/scout
> docker run -d --name scout --restart unless-stopped \
>   --env-file /opt/scout/scout.env -p 8080:8080 \
>   -v /opt/scout/data:/data aylantica-scout
> ```

- `--restart unless-stopped` — автоподъём после перезагрузки сервера.
- `-v /opt/scout/data:/data` — лиды и сессия переживают пересоздание
  контейнера (том, как Railway Volume).

## 3. Проверка (1 минута)

```bash
curl http://localhost/healthz          # на сервере (порт 8080)
curl http://localhost/status           # группы, классификатор, лимиты
curl -X POST http://localhost/scan     # первый скан
docker logs -f scout                   # жить в логах Telethon/ZAI
```

Снаружи — через домен: Timeweb → панель → DNS A-запись
`scout.вашдомен.ru → IP сервера`, потом Caddy/nginx на 80/443 (или просто
проброс порта в панели). Минимальный Caddyfile:

```
scout.вашдомен.ru {
    reverse_proxy 127.0.0.1:8080
}
```

## 4. Связь с Aylantica и E2E

Ровно как в Railway-инструкции (Шаги 6-7): в Vercel добавить
`SCOUT_SECRET` + `SCOUT_PUBLIC_URL=https://scout.вашдомен.ru`,
затем полная цепочка `healthz → scan → approve → лид в /settings →
Разведчик (Scout)`.

## Обслуживание

```bash
docker logs -f scout                      # логи
docker restart scout                      # рестарт
docker pull …/aylantica-scout:latest && \
  docker rm -f scout && …run…             # обновление
```

## Частые проблемы

| Симптом | Причина | Лечение |
|---|---|---|
| `AuthKeyUnregistered` | сессия умерла | перевыпустить `TELEGRAM_SESSION` (Railway-инструкция, Шаг 1) |
| `FloodWaitError` | Telegram rate-limit | `SEND_INTERVAL_SEC=3600` в scout.env |
| Лид не приходит в Aylantica | 401 — секреты не совпадают | сверить `AYLANTICA_SCOUT_SECRET` ↔ `SCOUT_SECRET` |
| Контейнер падает и встаёт | нет SESSION/API_ID | `docker logs scout` — что именно не задано |
