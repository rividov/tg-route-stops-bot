from dataclasses import dataclass
import os
from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Config:
    bot_token: str
    owner_id: int
    db_path: str
    weather_cache_ttl_minutes: int
    route_cache_ttl_minutes: int
    osrm_base_url: str


def load_config() -> Config:
    bot_token = os.getenv("BOT_TOKEN", "").strip()
    owner_raw = os.getenv("OWNER_ID", "").strip()
    db_path = os.getenv("DB_PATH", "data/route_bot.db").strip()
    w_ttl_raw = os.getenv("WEATHER_CACHE_TTL_MINUTES", "30").strip()
    r_ttl_raw = os.getenv("ROUTE_CACHE_TTL_MINUTES", "1440").strip()
    osrm_url = os.getenv("OSRM_BASE_URL", "https://router.project-osrm.org").strip().rstrip("/")

    if not bot_token:
        raise RuntimeError("Не задан BOT_TOKEN в .env")
    if not owner_raw.isdigit():
        raise RuntimeError("OWNER_ID должен быть числовым Telegram ID")

    w_ttl = int(w_ttl_raw) if w_ttl_raw.isdigit() else 30
    r_ttl = int(r_ttl_raw) if r_ttl_raw.isdigit() else 1440

    return Config(
        bot_token=bot_token,
        owner_id=int(owner_raw),
        db_path=db_path,
        weather_cache_ttl_minutes=max(5, w_ttl),
        route_cache_ttl_minutes=max(10, r_ttl),
        osrm_base_url=osrm_url or "https://router.project-osrm.org",
    )