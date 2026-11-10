"""Omborchining odatiy xabarini Gemini'siz o'qish.

Odatiy format (har biri alohida qatorda, tartib biroz farq qilishi mumkin):
    70 B 275 YA                      <- mashina raqami
    Shafoatov Shahzod                <- haydovchi
    +998946591422                    <- telefon ([TEL] bilan yashirilgan)
    200x300x625 ...40,5m³            <- o'lcham va hajm (bir nechta bo'lishi mumkin)
    18 paddon
    Gazablok kley                    <- kley / rodgips ...
    40 qop                           <- ... qop soni (shu yoki keyingi qatorda)
    Xojakbar mega qurilish mchj      <- xaridor
    Surxondaryo denov                <- viloyat + tuman

Hamma asosiy maydonlar topilsa — Dispatch qaytaradi, aks holda None (Gemini o'qiydi).
"""
import re

from rapidfuzz import fuzz, process

from parser import Dispatch, Line

# ---------------- lug'atlar ----------------

VILOYATLAR = {
    "Toshkent": ["toshkent", "tashkent", "тошкент", "ташкент"],
    "Andijon": ["andijon", "andijan", "андижон", "андижан"],
    "Buxoro": ["buxoro", "bukhara", "бухоро", "бухара"],
    "Farg'ona": ["farg'ona", "fargona", "fergana", "фаргона", "фарғона", "фергана"],
    "Jizzax": ["jizzax", "jizzakh", "жиззах", "джизак"],
    "Xorazm": ["xorazm", "khorezm", "хоразм", "хорезм"],
    "Namangan": ["namangan", "наманган"],
    "Navoiy": ["navoiy", "navoi", "навоий", "навои"],
    "Qashqadaryo": ["qashqadaryo", "kashkadarya", "қашқадарё", "кашкадарья", "кашкадарё"],
    "Qoraqalpog'iston": ["qoraqalpog'iston", "qoraqalpogiston", "karakalpakstan", "nukus",
                         "қорақалпоғистон", "каракалпакстан", "нукус"],
    "Samarqand": ["samarqand", "samarkand", "самарқанд", "самарканд"],
    "Sirdaryo": ["sirdaryo", "syrdarya", "сирдарё", "сырдарья", "гулистон", "guliston"],
    "Surxondaryo": ["surxondaryo", "surkhandarya", "сурхондарё", "сурхандарья", "termiz", "термез"],
}
_ALIAS = {a: v for v, al in VILOYATLAR.items() for a in al}

_FILLER = r"\b(viloyati|viloyat|vil|tumani|tuman|tum|shahri|shahar|sh|область|обл|район|р-н|город|г)\b\.?"

LEGAL = r"\b(mchj|m\.ch\.j|xk|qk|ok|aj|ooo|ооо|мчж|хк|ук|чп|ип|llc|ltd|firma|фирма)\b"

PLATE = re.compile(
    r"^\s*(\d{2})\s*([A-ZА-Я]\s*\d{3}\s*[A-ZА-Я]{2}|\d{3}\s*[A-ZА-Я]{3})\s*$", re.I)
SIZE = re.compile(r"(\d{2,4}(?:[.,]\d)?)\s*[x×*хХX]\s*(\d{2,4}(?:[.,]\d)?)\s*[x×*хХX]\s*(\d{2,4}(?:[.,]\d)?)")
M3 = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:m³|m3|м³|м3|kub|куб|kv\.?m)", re.I)
PIECES = re.compile(r"(\d+)\s*(?:dona|шт|штук|ta)\b", re.I)
PALLETS = re.compile(r"(\d+)\s*(?:paddon|padon|poddon|поддон|паддон|pallet|pal)", re.I)
BAGS = re.compile(r"(\d+)\s*(?:qop|kop|қоп|коп|мешок|меш)", re.I)
KLEY = re.compile(r"kley|klei|клей|клея", re.I)
RODGIPS = re.compile(r"rod\s*gips|rodgips|gips|гипс|rotband|ротбанд", re.I)
NAME = re.compile(r"^[A-Za-zА-Яа-яЎўҚқҒғҲҳʻ'‘’ .-]{3,}$")


def _num(s: str) -> float:
    return float(s.replace(",", "."))


def _title(s: str) -> str:
    return " ".join(w[:1].upper() + w[1:].lower() for w in s.split())


def _region(line: str) -> tuple[str, str] | None:
    """'Surxondaryo denov' -> ('Surxondaryo', 'Denov'); topilmasa None."""
    raw = line.lower().replace("ʻ", "'").replace("‘", "'").replace("’", "'")
    is_city = bool(re.search(r"\b(shahri|shahar|sh|город|г)\b", raw)) and "toshkent" in raw
    words = re.sub(_FILLER, " ", raw)
    words = re.sub(r"[,;:]", " ", words).split()
    if not words:
        return None
    for i, w in enumerate(words[:2]):
        hit = process.extractOne(w, list(_ALIAS), scorer=fuzz.ratio, score_cutoff=85)
        if hit:
            region = _ALIAS[hit[0]]
            if region == "Toshkent" and is_city:
                region = "Toshkent sh."
            rest = [x for j, x in enumerate(words) if j != i]
            return region, _title(" ".join(rest))
    return None


def quick_parse(text: str) -> Dispatch | None:
    lines = [l.strip(" .-—–\t") for l in (text or "").splitlines()]
    lines = [l for l in lines if l]
    if not lines:
        return None

    vehicle = buyer = ""
    region = district = ""
    total_pallets = 0
    items: list[Line] = []
    pending_bag_product: str | None = None   # "Gazablok kley" -> keyingi qatordagi "40 qop"
    leftovers: list[tuple[int, str]] = []
    region_cands: list = []

    for idx, l in enumerate(lines):
        low = l.lower()
        if "[tel]" in low:
            continue
        plate = None if vehicle else PLATE.match(l)
        if plate:
            rest = re.sub(r"\s+", "", plate.group(2).upper())
            m = re.match(r"([A-ZА-Я]?)(\d{3})([A-ZА-Я]+)", rest)
            vehicle = " ".join(x for x in (plate.group(1), *m.groups()) if x)
            continue

        bags = BAGS.search(l)
        kind = "kley" if KLEY.search(l) else ("rodgips" if RODGIPS.search(l) else None)
        if kind:
            if bags:
                items.append(Line(product=kind, bags=int(bags.group(1))))
            else:
                pending_bag_product = kind
            continue
        if bags and pending_bag_product:
            items.append(Line(product=pending_bag_product, bags=int(bags.group(1))))
            pending_bag_product = None
            continue

        size = SIZE.search(l)
        if size:
            m3 = M3.search(l[size.end():]) or M3.search(l)
            pcs = PIECES.search(l[size.end():])
            pal = PALLETS.search(l)
            dims = [_num(g) for g in size.groups()]
            if max(dims) < 100:                       # sm da yozilgan -> mm
                dims = [d * 10 for d in dims]
            items.append(Line(product="gazoblok", size="x".join(f"{d:g}" for d in dims),
                              m3=_num(m3.group(1)) if m3 else None,
                              pieces=int(pcs.group(1)) if pcs else None,
                              pallets=int(pal.group(1)) if pal else None))
            continue

        pal = PALLETS.search(l)
        if pal:
            total_pallets += int(pal.group(1))
            continue

        m3_only = M3.fullmatch(l.strip())
        if m3_only and items and items[-1].product == "gazoblok" and items[-1].m3 is None:
            items[-1].m3 = _num(m3_only.group(1))
            continue

        if re.search(LEGAL, low):
            buyer = l
            continue

        reg = _region(l)
        if reg:
            region_cands.append((idx, l, reg))
            continue

        leftovers.append((idx, l))

    # Manzil — odatda oxirgi qator: oxirgi mos keluvchi olinadi, qolganlari oddiy matn
    if region_cands:
        _, _, (region, district) = region_cands[-1]
        leftovers = sorted(leftovers + [(i, l) for i, l, _ in region_cands[:-1]])

    driver = ""
    texts = [l for _, l in leftovers if re.search(r"[A-Za-zА-Яа-я]{3}", l)]
    names = [l for l in texts if NAME.match(l) and 2 <= len(l.split()) <= 4]
    if buyer:                              # xaridor "mchj" bilan topildi
        driver = _title(names[0]) if names else ""
    elif len(texts) >= 2:                  # haydovchi + xaridor
        driver = _title(names[0]) if names and names[0] != texts[-1] else ""
        buyer = texts[-1]
    elif texts:                            # bitta nom — bu xaridor (muhimrog'i)
        buyer = texts[0]

    has_goods = any((i.product == "gazoblok" and (i.m3 or i.pieces)) or i.bags for i in items)
    if not (has_goods and buyer and region):
        return None
    return Dispatch(is_shipment=True, vehicle=vehicle, driver=driver, buyer=buyer.strip(),
                    region=region, district=district, pallets=total_pallets or None, lines=items)
