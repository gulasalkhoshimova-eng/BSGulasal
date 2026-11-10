"""Davr bo'yicha jo'natmalarni yig'ish, Excel va qisqa matn tayyorlash."""
import io
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from html import escape

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from rapidfuzz import fuzz

import db
from config import cfg
from parser import TZ

OYLAR = ["", "yanvar", "fevral", "mart", "aprel", "may", "iyun", "iyul", "avgust",
         "sentabr", "oktabr", "noyabr", "dekabr"]

D0 = Decimal(0)


# ---------------- normalizatsiya ----------------

_LEGAL = r"\b(mchj|xk|xususiy korxona|qk|ok|aj|ooo|ооо|мчж|ук|чп|ип|llc|ltd|firma|фирма)\b"


def buyer_key(name: str) -> str:
    s = (name or "").lower().replace("ʻ", "'").replace("‘", "'").replace("’", "'")
    s = re.sub(r"[\"'«»“”().,]", " ", s)
    s = re.sub(_LEGAL, " ", s)
    return re.sub(r"\s+", " ", s).strip()


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


def _kind(ln: dict) -> str:
    p = (ln.get("product") or "").lower()
    if "kley" in p or "клей" in p:
        return "kley"
    if "rodgips" in p or "gips" in p or "гипс" in p or "rotband" in p:
        return "rodgips"
    return "gazoblok" if (ln.get("size") or ln.get("m3") or "gazo" in p) else "boshqa"


# ---------------- yig'ish ----------------

@dataclass
class Row:  # bitta mashina
    sent_at: datetime
    vehicle: str
    driver: str
    phone: str
    buyer: str
    region: str
    district: str
    sizes: dict = field(default_factory=dict)   # o'lcham -> m3
    m3: Decimal = D0
    pallets: int = 0
    kley: int = 0
    rodgips: int = 0
    warnings: list = field(default_factory=list)
    link: str = ""


@dataclass
class Summary:
    d1: date
    d2: date
    title: str
    rows: list
    unread: list            # Gemini o'qiy olmagan xabarlar

    @property
    def start(self) -> datetime:
        return datetime.combine(self.d1, time(cfg.day_start_hour), TZ)

    @property
    def end(self) -> datetime:
        return datetime.combine(self.d2 + timedelta(days=1), time(cfg.day_end_hour), TZ)

    def by_size(self) -> dict:
        out = defaultdict(Decimal)
        for r in self.rows:
            for s, m in r.sizes.items():
                out[s] += m
        return dict(sorted(out.items(), key=lambda x: -x[1]))


def _link(chat_id: int, message_id: int) -> str:
    cid = str(chat_id)
    return f"https://t.me/c/{cid[4:]}/{message_id}" if cid.startswith("-100") else ""


def _merge_buyers(rows: list[Row]):
    """Bir xil xaridorni har xil yozilishini bitta nomga keltirish."""
    groups: list[tuple[str, list[Row]]] = []
    for r in rows:
        k = buyer_key(r.buyer)
        for gk, members in groups:
            if k and gk and fuzz.token_set_ratio(k, gk) >= 90:
                members.append(r)
                break
        else:
            groups.append((k, [r]))
    for _, members in groups:
        name = Counter(m.buyer for m in members if m.buyer).most_common(1)
        for m in members:
            m.buyer = name[0][0] if name else "Noma'lum xaridor"


def to_row(d: dict) -> Row:
    p = d["parsed"] or {}
    r = Row(sent_at=d["sent_at"].astimezone(TZ), vehicle=p.get("vehicle") or "",
            driver=p.get("driver") or "", phone=d.get("phone") or "",
            buyer=(p.get("buyer") or "").strip(), region=(p.get("region") or "").strip(),
            district=(p.get("district") or "").strip(), link=_link(d["chat_id"], d["message_id"]))
    line_pallets = 0
    for ln in p.get("lines", []):
        line_pallets += ln.get("pallets") or 0
        kind = _kind(ln)
        if kind in ("kley", "rodgips"):
            setattr(r, kind, getattr(r, kind) + (ln.get("bags") or ln.get("pieces") or 0))
            continue
        size = norm_size(ln.get("size")) or (ln.get("product") or "?")
        m3 = line_m3(ln)
        if m3 is None:
            r.warnings.append(f"{size}: m³ yozilmagan")
            continue
        r.sizes[size] = r.sizes.get(size, D0) + m3
        r.m3 += m3
    r.pallets = p.get("pallets") or line_pallets
    return r


async def collect(d1: date, d2: date, title: str) -> Summary:
    rows = [to_row(d) for d in await db.ok_between(d1, d2)]
    _merge_buyers(rows)
    return Summary(d1, d2, title, rows, await db.unread_between(d1, d2))


# ---------------- davrlar ----------------

def day_title(d: date) -> str:
    return f"{d.day}-{OYLAR[d.month]} kunlik chiqim"


def period_title(d1: date, d2: date, kind: str) -> str:
    return f"{kind}: {d1:%d.%m} – {d2:%d.%m.%Y}"


# ---------------- matn ----------------

def fmt(x) -> str:
    if isinstance(x, Decimal):
        s = f"{x.quantize(Decimal('0.01')):,.2f}".replace(",", " ").replace(".", ",")
        return s[:-3] if s.endswith(",00") else s.rstrip("0")
    return f"{x:,}".replace(",", " ")


def caption(s: Summary) -> str:
    lines = [f"📦 <b>{s.title}</b>",
             f"{s.start:%d.%m %H:%M} → {s.end:%d.%m %H:%M}", ""]
    if not s.rows:
        lines.append("Bu davrda jo'natma bo'lmadi.")
    else:
        lines.append(f"🚚 Mashinalar: <b>{len(s.rows)}</b>")
        for size, m3 in s.by_size().items():
            lines.append(f"▫️ {size} — {fmt(m3)} m³")
        lines.append(f"🧱 Jami gazoblok: <b>{fmt(sum((r.m3 for r in s.rows), D0))} m³</b>")
        lines.append(f"🪵 Paddon: <b>{fmt(sum(r.pallets for r in s.rows))}</b>")
        kley = sum(r.kley for r in s.rows)
        rodgips = sum(r.rodgips for r in s.rows)
        if kley:
            lines.append(f"🛍 Kley: <b>{fmt(kley)} qop</b>")
        if rodgips:
            lines.append(f"🛍 Rodgips: <b>{fmt(rodgips)} qop</b>")
        buyers = defaultdict(Decimal)
        for r in s.rows:
            buyers[r.buyer] += r.m3
        top = sorted(buyers.items(), key=lambda x: -x[1])[:5]
        lines.append("\n👥 Xaridorlar (top 5): " + "; ".join(
            f"{escape(b[:40])} — {fmt(m)} m³" for b, m in top))
    if s.unread:
        lines.append(f"\n⚠️ {len(s.unread)} ta xabar o'qilmadi — hisobotga kirmadi")
    warn = sum(len(r.warnings) for r in s.rows)
    if warn:
        lines.append(f"⚠️ {warn} ta qatorda m³ yozilmagan (Excel'da «Mashinalar» varag'i)")
    lines.append("\nBatafsil — Excel faylda.")
    # Telegram caption chegarasi 1024 belgi: sig'masa o'lchamlar/xaridorlar qisqartiriladi
    while len("\n".join(lines)) > 1024:
        idx = next((i for i, l in enumerate(lines) if l.startswith(("\n👥", "▫️"))), None)
        if idx is None:
            break
        lines.pop(idx)
    return "\n".join(lines)


# ---------------- Excel ----------------

HDR_FILL = PatternFill("solid", fgColor="1F4E78")
HDR_FONT = Font(bold=True, color="FFFFFF")
BOLD = Font(bold=True)


def _sheet(wb, title, header, rows, widths=None, total=None):
    ws = wb.create_sheet(title)
    ws.append(header)
    for c in ws[1]:
        c.fill, c.font = HDR_FILL, HDR_FONT
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for r in rows:
        ws.append([float(v) if isinstance(v, Decimal) else v for v in r])
    if total:
        ws.append([float(v) if isinstance(v, Decimal) else v for v in total])
        for c in ws[ws.max_row]:
            c.font = BOLD
    for i, w in enumerate(widths or [18] * len(header), 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A2"
    return ws


def excel(s: Summary) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Xulosa"
    ws.append([s.title])
    ws["A1"].font = Font(bold=True, size=14)
    ws.append([f"Davr: {s.start:%d.%m.%Y %H:%M} → {s.end:%d.%m.%Y %H:%M}"])
    ws.append([])
    summary_rows = [("Mashinalar soni", len(s.rows))]
    summary_rows += [(f"Gazoblok {k}, m³", float(v)) for k, v in s.by_size().items()]
    summary_rows += [
        ("Jami gazoblok, m³", float(sum((r.m3 for r in s.rows), D0))),
        ("Paddon, dona", sum(r.pallets for r in s.rows)),
        ("Kley, qop", sum(r.kley for r in s.rows)),
        ("Rodgips, qop", sum(r.rodgips for r in s.rows)),
        ("O'qilmagan xabarlar", len(s.unread)),
    ]
    for k, v in summary_rows:
        ws.append([k, v])
        if k.startswith("Jami"):
            ws.cell(ws.max_row, 1).font = ws.cell(ws.max_row, 2).font = BOLD
    ws.column_dimensions["A"].width, ws.column_dimensions["B"].width = 34, 16

    # Xaridorlar
    sizes = list(s.by_size())
    agg = defaultdict(lambda: {"n": 0, "sizes": defaultdict(Decimal), "m3": D0, "p": 0, "k": 0, "r": 0})
    for r in s.rows:
        a = agg[r.buyer]
        a["n"] += 1
        a["m3"] += r.m3
        a["p"] += r.pallets
        a["k"] += r.kley
        a["r"] += r.rodgips
        for sz, m in r.sizes.items():
            a["sizes"][sz] += m
    rows = [[b, a["n"], *[a["sizes"].get(sz, D0) or None for sz in sizes], a["m3"], a["p"], a["k"], a["r"]]
            for b, a in sorted(agg.items(), key=lambda x: -x[1]["m3"])]
    _sheet(wb, "Xaridorlar",
           ["Xaridor", "Mashina", *[f"{sz}, m³" for sz in sizes], "Jami m³", "Paddon", "Kley, qop", "Rodgips, qop"],
           rows, [34, 10, *[14] * len(sizes), 12, 10, 11, 12],
           ["JAMI", len(s.rows), *[s.by_size()[sz] for sz in sizes],
            sum((r.m3 for r in s.rows), D0), sum(r.pallets for r in s.rows),
            sum(r.kley for r in s.rows), sum(r.rodgips for r in s.rows)])

    # Manzillar
    dest = defaultdict(lambda: [0, D0, 0, 0, 0])
    for r in s.rows:
        x = dest[(r.region or "Yozilmagan", r.district or "")]
        x[0] += 1
        x[1] += r.m3
        x[2] += r.pallets
        x[3] += r.kley
        x[4] += r.rodgips
    _sheet(wb, "Manzillar", ["Viloyat", "Tuman", "Mashina", "Gazoblok m³", "Paddon", "Kley, qop", "Rodgips, qop"],
           [[k[0], k[1], *v] for k, v in sorted(dest.items(), key=lambda x: (x[0][0], -x[1][1]))],
           [20, 20, 10, 13, 10, 11, 12])

    # Mashinalar
    _sheet(wb, "Mashinalar",
           ["Sana, vaqt", "Mashina", "Haydovchi", "Telefon", "Xaridor", "Viloyat", "Tuman",
            "O'lchamlar", "Gazoblok m³", "Paddon", "Kley, qop", "Rodgips, qop", "Izoh", "Xabar"],
           [[r.sent_at.strftime("%d.%m.%Y %H:%M"), r.vehicle, r.driver, r.phone, r.buyer,
             r.region, r.district, "; ".join(f"{k} — {fmt(v)}" for k, v in r.sizes.items()),
             r.m3, r.pallets, r.kley, r.rodgips, "; ".join(r.warnings), r.link] for r in s.rows],
           [16, 14, 22, 16, 30, 14, 14, 28, 12, 9, 10, 11, 24, 30])

    if s.unread:
        _sheet(wb, "O'qilmagan",
               ["Sana, vaqt", "Xabar matni", "Xabar"],
               [[d["sent_at"].astimezone(TZ).strftime("%d.%m.%Y %H:%M"), d["raw_text"],
                 _link(d["chat_id"], d["message_id"])] for d in s.unread],
               [16, 70, 30])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def filename(s: Summary) -> str:
    return (f"chiqim_{s.d1:%Y-%m-%d}.xlsx" if s.d1 == s.d2
            else f"chiqim_{s.d1:%Y-%m-%d}_{s.d2:%Y-%m-%d}.xlsx")
