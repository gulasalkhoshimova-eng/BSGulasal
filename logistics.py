"""Logistika guruhidagi 'mashina chiqdi' xabarlarini yig'ish va kunlik hisobot.

Hisobot oynasi: kechagi 08:00 -> bugungi 07:00 (Asia/Tashkent).
"""
import re
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from html import escape
from zoneinfo import ZoneInfo

from google.genai import types
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

import db
from config import cfg
from ocr import extract

TZ = ZoneInfo(cfg.timezone)


# ---------------- Gemini sxemasi ----------------

class Line(BaseModel):
    product: str = Field(description="'gazoblok', 'kley' yoki boshqa mahsulot nomi")
    size: str = Field(default="", description="O'lcham mm da, 'x' bilan: masalan '600x300x200'. "
                                               "Kley yoki o'lchamsiz mahsulot uchun bo'sh")
    m3: float | None = Field(default=None, description="Agar xabarda m3/куб yozilgan bo'lsa")
    pieces: int | None = Field(default=None, description="Dona soni, agar yozilgan bo'lsa")
    bags: int | None = Field(default=None, description="Qop soni (kley uchun)")
    pallets: int | None = Field(default=None, description="Shu qatorga tegishli paddon soni")


class Shipment(BaseModel):
    is_shipment: bool = Field(description="Xabar mashina/yuk jo'natilgani haqidami? "
                                          "Salomlashish, savol, muhokama bo'lsa false")
    vehicle: str = Field(default="", description="Mashina davlat raqami, bo'lsa")
    buyer: str = Field(default="", description="Xaridor / mijoz nomi")
    destination: str = Field(default="", description="Manzil: faqat shahar yoki viloyat nomi, tuman/ko'chasiz, masalan 'Toshkent', 'Samarqand'")
    pallets: int | None = Field(default=None, description="Jami paddon soni, agar alohida yozilgan bo'lsa")
    lines: list[Line] = Field(default_factory=list)


PROMPT = """Bu gazoblok zavodining logistika Telegram guruhidagi xabar.
Agar u mashinaga yuk ortilib jo'natilgani haqida bo'lsa (is_shipment=true), ajrating:
mashina raqami, xaridor, manzil, mahsulotlar (o'lcham, m3, dona, kley qopi), paddon soni.
Qoidalar:
- O'lchamni har doim mm da va 'x' bilan yozing: "250*250*625", "25x25x62.5 sm" -> "250x250x625".
- "куб", "kub", "м3" = m3. Kley odatda qopda: "40 qop kley" -> product=kley, bags=40.
- "paddon", "поддон", "pallet" = paddon.
- Xabarda yo'q narsani o'ylab topmang, bo'sh qoldiring.
- Bitta xabarda bir nechta mashina bo'lsa ham, hammasini bitta yozuvga yig'ing.
Xabar:
"""


# ---------------- hisob-kitob ----------------

def norm_size(s: str) -> str:
    nums = re.findall(r"\d+(?:[.,]\d+)?", s or "")
    return "×".join(n.replace(",", ".") for n in nums[:3]) if len(nums) >= 3 else (s or "").strip()


def line_m3(ln: dict) -> Decimal | None:
    if ln.get("m3"):
        return Decimal(str(ln["m3"]))
    nums = re.findall(r"\d+(?:\.\d+)?", (ln.get("size") or "").replace(",", "."))
    if ln.get("pieces") and len(nums) >= 3:
        a, b, c = (Decimal(n) for n in nums[:3])
        return (a * b * c / Decimal(1_000_000_000) * ln["pieces"]).quantize(Decimal("0.001"))
    return None


def is_kley(ln: dict) -> bool:
    return "kley" in (ln.get("product") or "").lower() or "клей" in (ln.get("product") or "").lower()


def window_for(end_day: date) -> tuple[datetime, datetime]:
    """end_day 07:00 bilan tugaydigan oyna: (kechagi 08:00, end_day 07:00)."""
    end = datetime.combine(end_day, time(cfg.report_end_hour), TZ)
    start = datetime.combine(end_day - timedelta(days=1), time(cfg.report_start_hour), TZ)
    return start, end


def last_window_end_day(now: datetime | None = None) -> date:
    now = (now or datetime.now(TZ)).astimezone(TZ)
    return now.date() if now.hour >= cfg.report_end_hour else now.date() - timedelta(days=1)


# ---------------- saqlash ----------------

async def handle_message(chat_id: int, message_id: int, sent_at: datetime, text: str,
                         image: bytes | None = None) -> Shipment | None:
    """Xabarni saqlaydi va Gemini bilan o'qiydi. Gemini ishlamasa, keyin qayta o'qiladi."""
    await db.upsert_shipment_raw(chat_id, message_id, sent_at, text)
    try:
        sh = await _parse(text, image)
    except Exception:  # noqa: BLE001 — hisobot oldidan qayta urinamiz
        return None
    await db.save_shipment_parsed(chat_id, message_id, sh.is_shipment, Jsonb(sh.model_dump()))
    return sh


async def _parse(text: str, image: bytes | None) -> Shipment:
    contents = [PROMPT + (text or "(matn yo'q)")]
    if image:
        contents.insert(0, types.Part.from_bytes(data=image, mime_type="image/jpeg"))
    return await extract(contents, Shipment)


async def reparse_pending(start: datetime, end: datetime) -> int:
    """Gemini avval javob bermagan xabarlarni qayta o'qish. Qolgan sonini qaytaradi."""
    left = 0
    for r in await db.unparsed_shipments(start, end):
        try:
            sh = await _parse(r["raw_text"], None)
            await db.save_shipment_parsed(r["chat_id"], r["message_id"], sh.is_shipment,
                                          Jsonb(sh.model_dump()))
        except Exception:  # noqa: BLE001
            left += 1
    return left


# ---------------- hisobot ----------------

def _fmt(x: Decimal) -> str:
    return f"{x.quantize(Decimal('0.01')).normalize():f}".replace(".", ",")


async def build_report(end_day: date) -> str:
    start, end = window_for(end_day)
    left = await reparse_pending(start, end)
    rows = await db.shipments_between(start, end)

    by_size: dict[str, Decimal] = defaultdict(Decimal)
    size_unknown: dict[str, int] = defaultdict(int)   # m3 hisoblab bo'lmagan qatorlar
    kley_bags = 0
    pallets = 0
    by_buyer: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    by_dest: dict[str, list] = defaultdict(lambda: [0, Decimal(0)])
    vehicles = 0

    for r in rows:
        sh = r["parsed"]
        vehicles += 1
        line_pallets = 0
        total_m3 = Decimal(0)
        buyer = (sh.get("buyer") or "Noma'lum xaridor").strip()
        for ln in sh.get("lines", []):
            line_pallets += ln.get("pallets") or 0
            if is_kley(ln):
                kley_bags += ln.get("bags") or ln.get("pieces") or 0
                continue
            size = norm_size(ln.get("size")) or (ln.get("product") or "?")
            m3 = line_m3(ln)
            if m3 is None:
                size_unknown[size] += 1
                continue
            by_size[size] += m3
            by_buyer[buyer][size] += m3
            total_m3 += m3
        pallets += sh.get("pallets") or line_pallets
        dest = (sh.get("destination") or "Manzil yozilmagan").strip()
        by_dest[dest][0] += 1
        by_dest[dest][1] += total_m3

    hdr = (f"📦 <b>Kunlik chiqim</b>\n{start:%d.%m %H:%M} → {end:%d.%m %H:%M}\n"
           f"Mashinalar: <b>{vehicles}</b>")
    if not rows:
        return hdr + "\n\nBu davrda jo'natma xabari topilmadi."

    out = [hdr, "", "<b>1. O'lchamlar bo'yicha</b>"]
    for size, m3 in sorted(by_size.items(), key=lambda x: -x[1]):
        out.append(f"• {escape(size)} — {_fmt(m3)} m³")
    out.append(f"<b>Jami: {_fmt(sum(by_size.values(), Decimal(0)))} m³</b>")
    if kley_bags:
        out.append(f"Kley: <b>{kley_bags} qop</b>")
    for size, n in size_unknown.items():
        out.append(f"⚠️ {escape(size)}: {n} ta qatorda m³ yoki dona yozilmagan")

    out += ["", f"<b>2. Paddon:</b> {pallets} ta", "", "<b>3. Xaridorlar bo'yicha</b>"]
    for buyer, sizes in sorted(by_buyer.items(), key=lambda x: -sum(x[1].values())):
        parts = ", ".join(f"{escape(s)} — {_fmt(m)} m³" for s, m in sizes.items())
        out.append(f"• <b>{escape(buyer)}</b>: {parts} (jami {_fmt(sum(sizes.values(), Decimal(0)))} m³)")

    out += ["", "<b>4. Manzillar</b>"]
    for dest, (n, m3) in sorted(by_dest.items(), key=lambda x: -x[1][0]):
        out.append(f"• {escape(dest)} — {n} mashina, {_fmt(m3)} m³")

    if left:
        out.append(f"\n⚠️ {left} ta xabarni Gemini o'qiy olmadi — hisobotga kirmadi. "
                   f"Keyinroq /hisobot bilan qayta so'rang.")
    return "\n".join(out)
