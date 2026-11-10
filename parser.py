"""Omborchi xabarini Gemini orqali tuzilgan ma'lumotga aylantirish."""
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from google import genai
from google.genai import errors, types
from pydantic import BaseModel, Field

from config import cfg

TZ = ZoneInfo(cfg.timezone)


# ---------------- sxema ----------------

class Line(BaseModel):
    product: str = Field(description="Faqat bittasi: 'gazoblok', 'kley', 'rodgips' yoki 'boshqa'")
    size: str = Field(default="", description="Gazoblok o'lchami mm da, 'x' bilan, xabardagi tartibda: "
                                               "'200x300x625'. Kley/rodgips uchun bo'sh")
    m3: float | None = Field(default=None, description="Hajm m3 da (m³, kub, куб)")
    pieces: int | None = Field(default=None, description="Dona soni, agar yozilgan bo'lsa")
    bags: int | None = Field(default=None, description="Qop soni (kley, rodgips)")
    pallets: int | None = Field(default=None, description="Shu mahsulotga tegishli paddon soni")


class Dispatch(BaseModel):
    is_shipment: bool = Field(description="Xabar mashinaga yuk ortib jo'natilgani haqidami")
    vehicle: str = Field(default="", description="Mashina davlat raqami, masalan '70 B 275 YA'")
    driver: str = Field(default="", description="Haydovchi ismi familiyasi")
    buyer: str = Field(default="", description="Xaridor (firma) nomi, xabardagidek")
    region: str = Field(default="", description="Viloyat, o'zbekcha lotinda: 'Surxondaryo', 'Toshkent' ...")
    district: str = Field(default="", description="Tuman yoki shahar, o'zbekcha lotinda: 'Denov'")
    pallets: int | None = Field(default=None, description="Jami paddon soni")
    lines: list[Line] = Field(default_factory=list)


PROMPT = """Bu gazoblok zavodining logistika Telegram guruhidagi omborchi xabari.
Odatda bitta xabar = bitta mashina. Odatiy tartib (har biri alohida qatorda):
mashina raqami; haydovchi; telefon; o'lcham va hajm ("200x300x625 ...40,5m³");
paddon soni ("18 paddon"); kley ("Gazablok kley 40 qop"); xaridor ("... mchj"); manzil ("Surxondaryo denov").

Qoidalar:
- O'lchamni xabardagi tartibda, mm da, 'x' bilan yozing: "200*300*625" -> "200x300x625".
- "40,5m³" = 40.5. "kub", "куб", "м3" = m3.
- "Gazablok kley", "kley", "клей" -> product="kley". "rodgips", "rotband", "гипс" -> product="rodgips".
  Ular qopda: "40 qop" -> bags=40. Ular gazoblok EMAS.
- "paddon", "поддон", "pallet" = paddon.
- Manzil: viloyat va tuman alohida, o'zbekcha lotinda, bosh harf bilan ("Surxondaryo", "Denov").
  Toshkent shahri bo'lsa region="Toshkent sh.".
- Xabarda yo'q narsani o'ylab topmang — bo'sh qoldiring.
- Telefon raqamlar [TEL] bilan almashtirilgan — ularni e'tiborsiz qoldiring.

Xabar:
"""


# ---------------- yordamchilar ----------------

PHONE_RE = re.compile(r"(?:\+?998[\s\-()]*)?\(?\d{2}\)?[\s\-]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}")


def split_phone(text: str) -> tuple[str, str | None]:
    """Telefonni matndan ajratib oladi: Gemini'ga raqamsiz matn ketadi."""
    found = [m.group(0).strip() for m in PHONE_RE.finditer(text or "")
             if len(re.sub(r"\D", "", m.group(0))) in (9, 12)]
    masked = PHONE_RE.sub(lambda m: "[TEL]" if len(re.sub(r"\D", "", m.group(0))) in (9, 12)
                          else m.group(0), text or "")
    return masked, (", ".join(found) or None)


def work_day(ts: datetime) -> date | None:
    """Ish kuni: D 08:00 -> D+1 07:00. 07:00-08:00 oralig'i hech bir kunga kirmaydi."""
    t = ts.astimezone(TZ)
    if cfg.day_end_hour <= t.hour < cfg.day_start_hour:
        return None
    return (t - timedelta(hours=cfg.day_start_hour)).date()


def last_closed_day(now: datetime | None = None) -> date:
    """Hisoboti yuborilishi kerak bo'lgan oxirgi yopilgan ish kuni."""
    t = (now or datetime.now(TZ)).astimezone(TZ)
    return (t - timedelta(hours=cfg.day_end_hour)).date() - timedelta(days=1)


# ---------------- Gemini ----------------

_client = genai.Client(
    api_key=cfg.gemini_key,
    http_options=types.HttpOptions(
        timeout=90_000,
        retry_options=types.HttpRetryOptions(attempts=2, initial_delay=2, max_delay=5),
    ),
) if cfg.gemini_key else None

# Asosiy model band bo'lsa, keyingilari sinab ko'riladi
MODELS = [cfg.gemini_model] + [m for m in ("gemini-flash-latest", "gemini-3.7-flash")
                               if m != cfg.gemini_model]


async def parse(text: str, image: bytes | None = None) -> Dispatch:
    if _client is None:
        raise RuntimeError("GEMINI_API_KEY berilmagan")
    contents: list = [PROMPT + (text or "(matn yo'q)")]
    if image:
        contents.insert(0, types.Part.from_bytes(data=image, mime_type="image/jpeg"))
    last_err = None
    for model in MODELS:
        try:
            resp = await _client.aio.models.generate_content(
                model=model, contents=contents,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=Dispatch, temperature=0,
                    thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.LOW)),
            )
            break
        except (errors.ServerError, errors.ClientError) as e:
            if getattr(e, "code", None) not in (404, 429, 500, 503, 504):
                raise
            last_err = e
    else:
        raise RuntimeError(f"Gemini javob bermadi: {last_err}")
    return resp.parsed or Dispatch.model_validate_json(resp.text)
