# Scout — разведчик лидов Aylantica

**Scout** — отдельный Python-микросервис (FastAPI + Telethon), который читает
публичные Telegram-площадки шести вертикалей (заморозка, хлеб/HoReCa, кофе,
стройка, авто, клининг), находит сообщения-запросы («нужен поставщик»,
«где купить опт», «ищу подрядчика»), классифицирует их через **ZAI API**
(GLM), генерирует персональный черновик первого касания, отправляет
с жёсткими лимитами (прогрев, human-in-the-loop) и собирает ответы.

Качественных лидов Scout передаёт в платформу **Aylantica**
(`POST /api/scout/leads`) — там оркестратор создаёт тенанта-пакет
(сегмент, промпты, коммерческое предложение).

Scout ≠ часть Next.js. Это долгоживущий процесс с Telethon-сессией —
его место на Railway/Fly.io/VPS, не в serverless-превью.

## Зачем

Владелец платформы продаёт B2B-CRM шести вертикалям. Холодная аудитория
уже сама пишет в Telegram-чатах: «пеко­нет нужен поставщик муки»,
«ищем клининг для офиса 400 м²». Scout превращает эти сообщения в
тёплых лидов: с классификацией, скорингом и персональным черновиком —
чтобы владелец делал 10 качественных касаний в день, а не 100 спам-выходов.

## Архитектура (коротко)

```
scanner → classifier → generator → sender → inbox
(Telethon)  (ZAI GLM)   (шаблон+LLM) (лимиты)  (ответы)
                │                                │
                └── store (leads.json, inbox.json, sent_log.json)
                                                 │
                          Aylantica ← POST /api/scout/leads ← approve
```

Подробно: [ARCHITECTURE.md](ARCHITECTURE.md). Деплой за 15 минут:
[DEPLOY-RAILWAY.md](DEPLOY-RAILWAY.md) (Railway, рекомендуется) ·
[DEPLOY.md](DEPLOY.md) (Fly.io / VPS).

## API

| Метод | Ручка | Что делает |
|---|---|---|
| `POST` | `/scan` | Прогнать сканер по группам из `groups.json` (cron или вручную) |
| `GET` | `/leads` | Список лидов (фильтры: `status`, `category`) |
| `POST` | `/leads/{id}/approve` | Утвердить отправку (human-in-the-loop) |
| `POST` | `/leads/{id}/send` | Отправить черновик автору сообщения |
| `GET` | `/inbox` | Ответы на касания |
| `GET` | `/status` | Статус: uptime, лиды по статусам, лимиты отправки |
| `GET` | `/healthz` | Liveness-проба (для Railway) |

## Быстрый старт (локально)

```bash
cd scout

# 1. Виртуальное окружение
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. Переменные окружения
cp .env.example .env
# заполнить: TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_SESSION,
#            ZAI_API_KEY, AYLANTICA_API_URL

# 3. Единоразово — получить SESSION-строку для аккаунта-разведчика:
python -m scout.session_login
# (введите телефон @blaqadati и код подтверждения — строка появится в выводе)

# 4. Запуск
uvicorn main:app --host 0.0.0.0 --port 8080

# 5. Первое сканирование (парсит группы, классифицирует, складывает лидов)
curl -X POST localhost:8080/scan

# 6. Посмотреть лидов и утвердить лучший
curl localhost:8080/leads?status=classified
curl -X POST localhost:8080/leads/<ID>/approve
curl -X POST localhost:8080/leads/<ID>/send
```

## Где взять ключи

1. **TELEGRAM_API_ID / TELEGRAM_API_HASH** — [my.telegram.org](https://my.telegram.org)
   → *API development tools* → создать приложение (название любое, например
   `teleqatradelink`). Скопировать `app api_id` и `app api_hash`.
2. **TELEGRAM_SESSION** — строка сессии Telethon для аккаунта-разведчика
   (п. 3 выше). Аккаунт должен состоять в группах из `groups.json`
   (вступление — автоматически при первом скане, но для закрытых чатов
   лучше вступить руками с телефона).
3. **ZAI_API_KEY** — ключ Z.AI (GLM) для классификации и генерации.
4. **AYLANTICA_API_URL** — адрес деплоя платформы (например
   `https://aylantica-fryflow.vercel.app`) + `AYLANTICA_SCOUT_SECRET`
   (тот же секрет в ENV платформы).

## Лимиты и безопасность (важно!)

- **Human-in-the-loop**: ничего не уходит без `approve`.
- `DAILY_SEND_LIMIT` (по умолчанию 8) — касаний в день, не больше.
- `SEND_INTERVAL_SEC` (по умолчанию 1800) — минимум между касаниями.
- Прогрев `WARMUP_NO_LINK_DAYS` (по умолчанию 2): первые дни аккаунта —
  без ссылок, только текст.
- Пауза между двумя касаниями в одну группу — `GROUP_COOLDOWN_H` (по умолчанию 4 ч).
- Все запросы/отправки логируются в `data/sent_log.json` — перед глазами
  полная история, что уходило и кому.

## Тесты

```bash
cd scout && pip install -r requirements.txt && pytest -v
```

Тесты полностью офлайн: Telethon и ZAI замоканы, сеть не нужна.

## Структура

```
scout/
├── main.py          # FastAPI: ручки, cron-цикл, оркестрация
├── config.py        # ENV → настройки, загрузка groups.json
├── scanner.py       # Telethon: чтение групп/каналов
├── classifier.py    # ZAI GLM: лид или шум + скоринг (fallback — эвристика)
├── generator.py     # черновик касания: шаблон вертикали + LLM-полировка
├── sender.py        # отправка с лимитами/прогревом/кулдаунами
├── inbox.py         # ответы: DM-диалоги + Bot API getUpdates
├── store.py         # JSON-хранилище: лиды, инбокс, лог отправок
├── session_login.py # одноразовый скрипт получения TELEGRAM_SESSION
├── groups.json      # реестр площадок (проверено 2026-11-04)
├── tests/           # pytest: scanner, classifier, sender, generator, store
├── requirements.txt
├── Dockerfile
├── .env.example
├── README.md        # этот файл
├── ARCHITECTURE.md  # схема и потоки данных
└── DEPLOY.md        # Railway / Fly.io / VPS
```
