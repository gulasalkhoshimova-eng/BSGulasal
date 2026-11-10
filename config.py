import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _ids(raw: str) -> frozenset[int]:
    return frozenset(int(x) for x in raw.replace(" ", "").split(",") if x)


@dataclass(frozen=True)
class Config:
    telegram_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    gemini_key: str = os.getenv("GEMINI_API_KEY", "")
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
    database_url: str = os.getenv("DATABASE_URL", "postgresql://localhost:5432/nakladnoy")
    # Qo'shimcha ruxsat etilgan foydalanuvchilar (asosiysi — botga birinchi /start yozgan admin)
    allowed_users: frozenset = field(default_factory=lambda: _ids(os.getenv("ALLOWED_USER_IDS", "")))
    timezone: str = os.getenv("TZ_NAME", "Asia/Tashkent")
    # Ish kuni: D kuni 08:00 -> D+1 kuni 07:00. Hisobot 07:00 da yuboriladi.
    day_start_hour: int = int(os.getenv("REPORT_START_HOUR", "8"))
    day_end_hour: int = int(os.getenv("REPORT_END_HOUR", "7"))


cfg = Config()
