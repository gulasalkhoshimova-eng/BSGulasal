"""1C 'Поступление материалов' uchun CSV.

Har bir tovar qatori = CSV'ning bitta qatori; hujjat maydonlari (sana, kontragent,
shartnoma...) har qatorda takrorlanadi. Ustun nomlarini 1C'dagi yuklash
обработкаsi kutganiga moslash uchun faqat COLUMNS ro'yxatini o'zgartiring.
"""
import csv
import io
from decimal import Decimal

from config import cfg

COLUMNS = [
    "Дата",
    "Контрагент",
    "ИНН",
    "Договор",
    "Сумма документа",
    "Валюта",
    "Подразделение",
    "Ставка НДС",
    "№ вх.док-та",
    "Дата вх.док-та",
    "Комментарий",
    "Номенклатура",
    "Ед.изм",
    "Количество",
    "Цена",
    "Сумма",
]


def _num(x) -> str:
    v = Decimal(str(x)).normalize()
    s = format(v, "f")
    return s.replace(".", ",")  # 1C va rus Excel kasr uchun vergul kutadi


def build_csv(invoices: list[dict]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=cfg.csv_delimiter, quoting=csv.QUOTE_MINIMAL)
    w.writerow(COLUMNS)
    for inv in invoices:
        dt = inv["doc_date"].strftime("%d.%m.%Y") if inv["doc_date"] else ""
        doc_sum = sum((Decimal(str(i["amount"])) for i in inv["items"]), Decimal(0))
        for it in inv["items"]:
            w.writerow([
                dt,
                inv.get("k_name") or inv["supplier_raw"],
                inv.get("k_inn") or "",
                inv.get("k_contract") or "",
                _num(doc_sum),
                cfg.currency,
                cfg.podrazdelenie,
                cfg.nds,
                inv["doc_number"] or "",
                dt,
                inv["comment"] or "",
                it["name"],
                it["unit"] or "",
                _num(it["qty"]),
                _num(it["price"]),
                _num(it["amount"]),
            ])
    # UTF-8 BOM — Excel/1C kirillchani to'g'ri ochishi uchun
    return ("﻿" + buf.getvalue()).encode("utf-8")
