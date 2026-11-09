import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _ids(raw: str) -> set[int]:
    return {int(x) for x in raw.replace(" ", "").split(",") if x}


@dataclass(frozen=True)
class Config:
    telegram_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    gemini_key: str = os.getenv("GEMINI_API_KEY", "")
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
    database_url: str = os.getenv("DATABASE_URL", "postgresql://localhost:5432/nakladnoy")
    allowed_users: frozenset = frozenset(_ids(os.getenv("ALLOWED_USER_IDS", "")))
    podrazdelenie: str = os.getenv("DEFAULT_PODRAZDELENIE", "склад снабжения")
    nds: str = os.getenv("DEFAULT_NDS", "Без НДС")
    currency: str = os.getenv("DEFAULT_CURRENCY", "UZS")
    csv_delimiter: str = os.getenv("CSV_DELIMITER", ";")
    # Logistika hisoboti: kechagi 08:00 -> bugungi 07:00, 07:00 da yuboriladi
    timezone: str = os.getenv("TZ_NAME", "Asia/Tashkent")
    report_start_hour: int = int(os.getenv("REPORT_START_HOUR", "8"))
    report_end_hour: int = int(os.getenv("REPORT_END_HOUR", "7"))


cfg = Config()
