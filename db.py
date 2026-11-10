from datetime import date
from pathlib import Path

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from config import cfg

pool = AsyncConnectionPool(cfg.database_url, open=False, kwargs={"row_factory": dict_row})


async def init():
    await pool.open()
    sql = (Path(__file__).parent / "schema.sql").read_text()
    async with pool.connection() as c:
        await c.execute(sql)


async def _one(q, args=()):
    async with pool.connection() as c:
        return await (await c.execute(q, args)).fetchone()


async def _all(q, args=()):
    async with pool.connection() as c:
        return await (await c.execute(q, args)).fetchall()


async def _exec(q, args=()):
    async with pool.connection() as c:
        await c.execute(q, args)


# ---------- sozlamalar ----------

async def get_setting(key: str) -> str | None:
    r = await _one("SELECT value FROM settings WHERE key=%s", (key,))
    return r["value"] if r else None


async def set_setting(key: str, value: str):
    await _exec("INSERT INTO settings (key, value) VALUES (%s,%s) "
                "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value", (key, value))


# ---------- foydalanuvchilar ----------

async def get_user(uid: int) -> dict | None:
    return await _one("SELECT * FROM users WHERE user_id=%s", (uid,))


async def has_admin() -> bool:
    return bool(await _one("SELECT 1 FROM users WHERE role='admin' LIMIT 1"))


async def add_user(uid: int, name: str | None, role: str = "user"):
    await _exec("""INSERT INTO users (user_id, name, role) VALUES (%s,%s,%s)
                   ON CONFLICT (user_id) DO UPDATE SET name=COALESCE(EXCLUDED.name, users.name)""",
                (uid, name, role))


async def remove_user(uid: int) -> bool:
    async with pool.connection() as c:
        cur = await c.execute("DELETE FROM users WHERE user_id=%s AND role<>'admin'", (uid,))
        return cur.rowcount > 0


async def set_subscribed(uid: int, on: bool):
    await _exec("UPDATE users SET subscribed=%s WHERE user_id=%s", (on, uid))


async def list_users() -> list[dict]:
    return await _all("SELECT * FROM users ORDER BY role, created_at")


async def subscribers() -> list[int]:
    return [r["user_id"] for r in await _all("SELECT user_id FROM users WHERE subscribed")]


async def admins() -> list[int]:
    return [r["user_id"] for r in await _all("SELECT user_id FROM users WHERE role='admin'")]


# ---------- jo'natmalar ----------

async def get_dispatch(chat_id: int, message_id: int) -> dict | None:
    return await _one("SELECT * FROM dispatches WHERE chat_id=%s AND message_id=%s",
                      (chat_id, message_id))


async def upsert_raw(chat_id, message_id, sent_at, work_day, text, phone):
    """Yangi yoki tahrirlangan xabar: qayta o'qish uchun 'pending' bo'ladi."""
    await _exec(
        """INSERT INTO dispatches (chat_id, message_id, sent_at, work_day, raw_text, phone)
           VALUES (%s,%s,%s,%s,%s,%s)
           ON CONFLICT (chat_id, message_id) DO UPDATE
           SET raw_text=EXCLUDED.raw_text, phone=EXCLUDED.phone, status='pending',
               attempts=0, updated_at=now()""",
        (chat_id, message_id, sent_at, work_day, text, phone))


async def save_parsed(chat_id, message_id, status: str, parsed: dict | None):
    await _exec("""UPDATE dispatches SET status=%s, parsed=%s, updated_at=now()
                   WHERE chat_id=%s AND message_id=%s""",
                (status, Jsonb(parsed) if parsed is not None else None, chat_id, message_id))


async def mark_failed(chat_id, message_id):
    await _exec("""UPDATE dispatches SET status='failed', attempts=attempts+1, updated_at=now()
                   WHERE chat_id=%s AND message_id=%s""", (chat_id, message_id))


async def set_status(chat_id, message_id, status: str):
    await _exec("UPDATE dispatches SET status=%s, updated_at=now() "
                "WHERE chat_id=%s AND message_id=%s", (status, chat_id, message_id))


async def to_retry(max_attempts: int = 12) -> list[dict]:
    return await _all("""SELECT * FROM dispatches
                         WHERE status IN ('pending','failed') AND attempts < %s
                         ORDER BY sent_at""", (max_attempts,))


async def unread_between(d1: date, d2: date) -> list[dict]:
    return await _all("""SELECT * FROM dispatches WHERE status IN ('pending','failed')
                         AND work_day BETWEEN %s AND %s ORDER BY sent_at""", (d1, d2))


async def ok_between(d1: date, d2: date) -> list[dict]:
    return await _all("""SELECT * FROM dispatches WHERE status='ok'
                         AND work_day BETWEEN %s AND %s ORDER BY sent_at""", (d1, d2))
