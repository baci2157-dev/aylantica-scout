"""Конфигурация Scout: ENV → настройки + реестр групп (groups.json)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

SCOUT_DIR = Path(__file__).resolve().parent


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class GroupEntry:
    """Площадка для сканирования (группа или канал)."""

    username: str
    name: str
    category: str
    type: str = "group"  # group | channel
    invite: str | None = None
    estimated_members: int = 0
    note: str = ""

    @property
    def ref(self) -> str:
        """Адрес для Telethon: username или invite-хэш."""
        if self.invite:
            return self.invite
        return self.username


@dataclass(frozen=True)
class Settings:
    # Telegram (аккаунт-разведчик)
    telegram_api_id: int = 0
    telegram_api_hash: str = ""
    telegram_session: str = ""
    # ZAI
    zai_api_key: str = ""
    zai_model: str = "glm-4.6"
    zai_base_url: str = "https://api.z.ai/api/paas/v4"
    # Aylantica
    aylantica_api_url: str = ""
    aylantica_scout_secret: str = ""
    # Бот и владелец
    scout_bot_token: str = ""
    owner_telegram_id: int = 0
    # Скан
    scan_window_h: int = 12
    scan_messages_limit: int = 200
    min_score: int = 6
    # Лимиты отправки
    daily_send_limit: int = 8
    send_interval_sec: int = 1800
    warmup_no_link_days: int = 2
    group_cooldown_h: float = 4.0
    user_cooldown_days: int = 14
    # Сервис
    port: int = 8080
    data_dir: str = str(SCOUT_DIR / "data")
    groups: tuple[GroupEntry, ...] = field(default_factory=tuple)

    @property
    def has_telegram(self) -> bool:
        return bool(self.telegram_api_id and self.telegram_api_hash and self.telegram_session)

    @property
    def has_zai(self) -> bool:
        return bool(self.zai_api_key)


def load_groups(path: Path | None = None) -> tuple[GroupEntry, ...]:
    """Читает groups.json → кортеж GroupEntry (порядок сохранён)."""
    path = path or SCOUT_DIR / "groups.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    return tuple(
        GroupEntry(
            username=g["username"],
            name=g["name"],
            category=g["category"],
            type=g.get("type", "group"),
            invite=g.get("invite"),
            estimated_members=int(g.get("estimated_members", 0)),
            note=g.get("note", ""),
        )
        for g in raw.get("groups", [])
    )


def load_settings() -> Settings:
    """ENV + groups.json → Settings (иммутабельный объект)."""
    return Settings(
        telegram_api_id=_env_int("TELEGRAM_API_ID", 0),
        telegram_api_hash=os.getenv("TELEGRAM_API_HASH", ""),
        telegram_session=os.getenv("TELEGRAM_SESSION", ""),
        zai_api_key=os.getenv("ZAI_API_KEY", ""),
        zai_model=os.getenv("ZAI_MODEL", "glm-4.6"),
        zai_base_url=os.getenv("ZAI_BASE_URL", "https://api.z.ai/api/paas/v4"),
        aylantica_api_url=os.getenv("AYLANTICA_API_URL", "").rstrip("/"),
        aylantica_scout_secret=os.getenv("AYLANTICA_SCOUT_SECRET", ""),
        scout_bot_token=os.getenv("SCOUT_BOT_TOKEN", ""),
        owner_telegram_id=_env_int("OWNER_TELEGRAM_ID", 0),
        scan_window_h=_env_int("SCAN_WINDOW_H", 12),
        scan_messages_limit=_env_int("SCAN_MESSAGES_LIMIT", 200),
        min_score=_env_int("MIN_SCORE", 6),
        daily_send_limit=_env_int("DAILY_SEND_LIMIT", 8),
        send_interval_sec=_env_int("SEND_INTERVAL_SEC", 1800),
        warmup_no_link_days=_env_int("WARMUP_NO_LINK_DAYS", 2),
        group_cooldown_h=_env_float("GROUP_COOLDOWN_H", 4.0),
        user_cooldown_days=_env_int("USER_COOLDOWN_DAYS", 14),
        port=_env_int("PORT", 8080),
        data_dir=os.getenv("DATA_DIR", str(SCOUT_DIR / "data")),
        groups=load_groups(),
    )


# Ключевые слова грубого фильтра сканера: запросы — наверх, шум — мимо.
DEMAND_KEYWORDS: tuple[str, ...] = (
    "нужен", "нужна", "нужно", "ищу", "ищем", "посоветуйте", "порекомендуйте",
    "где купить", "где закупить", "who is", "поставщик", "поставщиков",
    "опт", "оптом", "закупить", "куплю", "заказ", "подрядчик", "исполнителя",
    "снабженец", "снабжение", "смета", "просчет", "прайс", "прайс-лист",
)
NOISE_KEYWORDS: tuple[str, ...] = (
    "вакансия", "вакансии", "ищу работу", "резюме", "продам", "продаю",
    "продаём", "продажа", "размещу рекламу", "kremlin", "casino", "казино",
    "ставки", "букмекер", "forex", "обнал", "займ", "микрозайм",
)

# Категория → уточняющие слова (когда площадка смешанная).
CATEGORY_HINTS: dict[str, tuple[str, ...]] = {
    "freezing": ("картофел", "фри", "заморожен", "заморозк", "пюре", "наггетс",
                 "овощ", "фрукт", "мясо", "птиц", "рыб", "полуфабрикат", "упаковк"),
    "bread": ("хлеб", "булк", "выпечк", "батон", "мука", "тесто", "пекарн",
              "кондитер", "круассан", "багет", "лаваш"),
    "coffee": ("кофе", "кофейн", "бариста", "зерно", "обжарк", "эспрессо",
               "латте", "кофемашин", "сироп"),
    "construction": ("стройк", "строитель", "ремонт", "отделк", "объект",
                     "бригад", "прораб", "смет", "материал", "электрик",
                     "сантехник", "штукатур", "стяжк", "кровл"),
    "auto": ("автосервис", "сто", "ремонт авто", "автомастер", "малярк",
             "кузовн", "диагност", "шиномонтаж", "запчаст", "масл", "фильтр"),
    "cleaning": ("клининг", "уборк", "уборщиц", "мойк", "химчистк",
                 "помывк", "дезинфекц", "cleaning"),
}

# ── Вертикали платформы Aylantica (v2.2, Блок 2) ────────────────────────
# Категория площадки Scout → вертикаль тенанта платформы. Полное покрытие
# 6 категорий: продукты (3 вида) → food_distribution, авто → autoservice,
# клининг → services, стройка → non_food_distribution.

CATEGORY_TO_VERTICAL: dict[str, str] = {
    "freezing": "food_distribution",
    "bread": "food_distribution",
    "coffee": "food_distribution",
    "auto": "autoservice",
    "cleaning": "services",
    "construction": "non_food_distribution",
}

# Человекочитаемые подписи вертикальных категорий (UI/логи).
CATEGORY_LABELS: dict[str, str] = {
    "freezing": "Заморозка / общепит",
    "bread": "Пекарни",
    "coffee": "Кофейни",
    "construction": "Строительство",
    "auto": "Автосервисы",
    "cleaning": "Клининг",
}

VERTICAL_LABELS: dict[str, str] = {
    "food_distribution": "Продукты (дистрибуция)",
    "autoservice": "Автосервисы",
    "services": "Услуги",
    "non_food_distribution": "Строительство и материалы",
}


def category_to_vertical(category: str) -> str:
    """Категория площадки → вертикаль платформы (неизвестное → services)."""
    return CATEGORY_TO_VERTICAL.get(category, "services")


def category_label(category: str) -> str:
    """Подпись категории для логов/бота."""
    return CATEGORY_LABELS.get(category, category)
