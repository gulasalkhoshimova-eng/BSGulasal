from datetime import date
from decimal import Decimal
from pathlib import Path

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool
from rapidfuzz import fuzz, process

from config import cfg

pool = AsyncConnectionPool(cfg.database_url, open=False, kwargs={"row_factory": dict_row})


async def init():
    await pool.open()
    sql = (Path(__file__).parent / "schema.sql").read_text()
    async with pool.connection() as c:
        await c.execute(sql)


# ---------- kontragentlar ----------

async def add_contractor(name: str, inn: str | None, contract: str | None, aliases: list[str]):
    async with pool.connection() as c:
        await c.execute(
            """INSERT INTO contractors (name, inn, contract, aliases) VALUES (%s,%s,%s,%s)
               ON CONFLICT (name) DO UPDATE SET inn=EXCLUDED.inn, contract=EXCLUDED.contract,
               aliases=EXCLUDED.aliases""",
            (name, inn, contract, aliases),
        )


async def list_contractors() -> list[dict]:
    async with pool.connection() as c:
        return await (await c.execute("SELECT * FROM contractors ORDER BY name")).fetchall()


async def delete_contractor(cid: int) -> bool:
    async with pool.connection() as c:
        cur = await c.execute("DELETE FROM contractors WHERE id=%s", (cid,))
        return cur.rowcount > 0


async def match_contractor(text: str) -> dict | None:
    """Nakladnoydagi 'Kimdan' matnini 1C kontragentiga moslash (taxminiy)."""
    rows = await list_contractors()
    choices = {}
    for r in rows:
        for v in [r["name"], *r["aliases"]]:
            choices[v] = r
    if not choices or not text:
        return None
    best = process.extractOne(text, list(choices), scorer=fuzz.token_set_ratio)
    if best and best[1] >= 75:
        return choices[best[0]]
    return None


# ---------- nakladnoylar ----------

async def save_invoice(user_id, inv, contractor_id, photo_file_id, raw: dict) -> int:
    async with pool.connection() as c, c.transaction():
        row = await (await c.execute(
            """INSERT INTO invoices (user_id, doc_date, doc_number, supplier_raw, buyer_raw,
                   contractor_id, total, photo_file_id, raw_json)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
            (user_id, inv["date"], inv["number"] or None, inv["supplier"], inv["buyer"],
             contractor_id, inv["total"], photo_file_id, Jsonb(raw)),
        )).fetchone()
        await _write_items(c, row["id"], inv["items"])
        return row["id"]


async def _write_items(c, invoice_id, items):
    await c.execute("DELETE FROM invoice_items WHERE invoice_id=%s", (invoice_id,))
    for i, it in enumerate(items, 1):
        await c.execute(
            """INSERT INTO invoice_items (invoice_id, line_no, name, unit, qty, price, amount)
               VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (invoice_id, i, it["name"], it["unit"], it["qty"], it["price"], it["amount"]),
        )


async def replace_items(invoice_id: int, items: list[dict]):
    async with pool.connection() as c, c.transaction():
        await _write_items(c, invoice_id, items)


async def get_invoice(invoice_id: int) -> dict | None:
    async with pool.connection() as c:
        inv = await (await c.execute(
            """SELECT i.*, k.name AS k_name, k.inn AS k_inn, k.contract AS k_contract
               FROM invoices i LEFT JOIN contractors k ON k.id=i.contractor_id WHERE i.id=%s""",
            (invoice_id,),
        )).fetchone()
        if inv:
            inv["items"] = await (await c.execute(
                "SELECT * FROM invoice_items WHERE invoice_id=%s ORDER BY line_no", (invoice_id,)
            )).fetchall()
        return inv


async def update_invoice(invoice_id: int, **fields):
    allowed = {"doc_date", "doc_number", "contractor_id", "total", "comment", "status"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    if not fields:
        return
    sets = ", ".join(f"{k}=%s" for k in fields)
    async with pool.connection() as c:
        await c.execute(f"UPDATE invoices SET {sets} WHERE id=%s", (*fields.values(), invoice_id))


async def pending_export(user_id: int, day: date | None) -> list[dict]:
    """Tasdiqlangan, hali eksport qilinmagan nakladnoylar."""
    q = "SELECT id FROM invoices WHERE user_id=%s AND status='confirmed'"
    args: list = [user_id]
    if day:
        q += " AND created_at::date=%s"
        args.append(day)
    async with pool.connection() as c:
        ids = [r["id"] for r in await (await c.execute(q + " ORDER BY id", args)).fetchall()]
    return [await get_invoice(i) for i in ids]


async def mark_exported(ids: list[int]):
    async with pool.connection() as c:
        await c.execute(
            "UPDATE invoices SET status='exported', exported_at=now() WHERE id = ANY(%s)", (ids,)
        )


def items_sum(items) -> Decimal:
    return sum((Decimal(str(i["amount"])) for i in items), Decimal(0))
