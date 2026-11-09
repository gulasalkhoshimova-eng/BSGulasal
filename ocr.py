"""Nakladnoy rasmini Gemini orqali tuzilgan ma'lumotga aylantirish."""
from decimal import Decimal

from google import genai
from google.genai import errors, types
from pydantic import BaseModel, Field

from config import cfg


class Item(BaseModel):
    name: str = Field(description="Tovar nomi, nakladnoydagidek (kirillcha)")
    unit: str = Field(description="O'lchov birligi: шт, блок, кг, м, пачка ...")
    qty: float
    price: float = Field(description="Bir dona narxi, so'mda, nuqta/bo'shliqsiz son")
    amount: float = Field(description="Qator summasi, so'mda")


class Invoice(BaseModel):
    date: str = Field(description="Sana ISO formatda: YYYY-MM-DD")
    number: str = Field(default="", description="Nakladnoy raqami, bo'lmasa bo'sh")
    supplier: str = Field(description="'Kimdan / От кого' maydoni")
    buyer: str = Field(description="'Kimga / Кому' maydoni")
    items: list[Item]
    total: float = Field(description="'Итого' summasi, bo'lmasa qatorlar yig'indisi")


PROMPT = """Bu O'zbekistondagi qo'lda yozilgan yuk xati (накладная) rasmi.
Jadvaldagi har bir to'ldirilgan qatorni o'qing: Наименование, Ед.изм, Кол-во, Цена, Сумма.
Qoidalar:
- Bo'sh va chizib tashlangan qatorlarni tashlab keting.
- Raqamlardagi nuqta/bo'shliq minglik ajratuvchi: "250.000" = 250000, "1.200.000" = 1200000.
- Sana odatda kun-oy-yil tartibida yozilgan ("3-11-2026" = 2026-11-03).
- Matnni kirillchada, rasmda qanday bo'lsa shunday yozing, tarjima qilmang.
- Ishonchingiz komil bo'lmagan raqamni qty*price=amount tengligi bilan tekshiring.
"""

_client = genai.Client(
    api_key=cfg.gemini_key,
    http_options=types.HttpOptions(
        timeout=60_000,
        retry_options=types.HttpRetryOptions(attempts=2, initial_delay=2, max_delay=5),
    ),
) if cfg.gemini_key else None

# Asosiy model band bo'lsa, navbatdagilari sinab ko'riladi
MODELS = [cfg.gemini_model] + [
    m for m in ("gemini-flash-latest", "gemini-3.7-flash", "gemini-3.5-flash")
    if m != cfg.gemini_model
]


async def read_invoice(image: bytes, mime: str = "image/jpeg") -> Invoice:
    if _client is None:
        raise RuntimeError("GEMINI_API_KEY .env faylida yo'q")
    last_err = None
    for model in MODELS:
        try:
            resp = await _client.aio.models.generate_content(
                model=model,
                contents=[types.Part.from_bytes(data=image, mime_type=mime), PROMPT],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=Invoice,
                    temperature=0,
                ),
            )
            break
        except (errors.ServerError, errors.ClientError) as e:
            # 503 band / 404 model yopilgan / 429 limit -> keyingi modelga o'tamiz
            if getattr(e, "code", None) not in (404, 429, 500, 503, 504):
                raise
            last_err = e
    else:
        raise RuntimeError(f"Gemini hozir javob bermayapti: {last_err}")
    if resp.parsed is None:
        return Invoice.model_validate_json(resp.text)
    return resp.parsed


def d(x) -> Decimal:
    return Decimal(str(x)).quantize(Decimal("0.01"))
