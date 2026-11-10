"""Logistika guruhidan kunlik / haftalik / 15 kunlik / oylik chiqim hisoboti. Telegram bot."""
import calendar
import logging
import time as _time
from datetime import date, datetime, timedelta
from datetime import time as dtime
from html import escape
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile, Update
from telegram.constants import ParseMode
from telegram.error import Conflict
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters,
)

import db
import report
from config import cfg
from parser import TZ, last_closed_day, parse, split_phone, work_day

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("bot")

MAX_ATTEMPTS = 12          # Gemini'ga qayta urinishlar (har 10 daqiqada) ≈ 2 soat

HELP = """<b>Logistika hisobot boti</b>

Bot logistika guruhidagi omborchi xabarlarini o‘qiydi va hisobot yuboradi:
• har kuni <b>07:00</b> — kunlik (kecha 08:00 → bugun 07:00)
• har <b>dushanba</b> — haftalik
• har oyning <b>16</b>- va <b>1</b>-kuni — 15 kunlik
• har oyning <b>1</b>-kuni — oylik

<b>Buyruqlar</b>
/hisobot — oxirgi kunlik hisobot
/hisobot 09.11.2026 — shu ish kuni (09.11 08:00 → 10.11 07:00)
/bugun — joriy kun (hozirgacha)
/hafta — o‘tgan hafta
/yarim_oy — oxirgi 15 kunlik
/oy — o‘tgan oy
/obuna — avtomatik hisobotni yoqish/o‘chirish

<b>Guruhda</b>
/guruh — shu guruhni kuzatish (bir marta)
Noto‘g‘ri xabarga <b>javob (reply)</b> qilib /bekor — hisobotdan chiqarish"""

ADMIN_HELP = """

<b>Admin</b>
/foydalanuvchilar — ro‘yxat
/olib_tashlash ID — foydalanuvchini o‘chirish
/holat — bot holati"""


# ================= yordamchilar =================

async def is_member(uid: int) -> bool:
    return uid in cfg.allowed_users or await db.get_user(uid) is not None


async def is_admin(uid: int) -> bool:
    u = await db.get_user(uid)
    return bool(u and u["role"] == "admin")


async def notify(bot, ids, text: str, **kw):
    for uid in ids:
        try:
            await bot.send_message(uid, text, parse_mode=ParseMode.HTML,
                                   disable_web_page_preview=True, **kw)
        except Exception as e:  # noqa: BLE001
            log.warning("Xabar yuborilmadi %s: %s", uid, e)


async def send_summary(bot, s: report.Summary, ids, prefix: str = ""):
    data = report.excel(s)
    cap = (prefix + "\n" if prefix else "") + report.caption(s)
    for uid in ids:
        try:
            await bot.send_document(uid, InputFile(data, filename=report.filename(s)),
                                    caption=cap[:1024], parse_mode=ParseMode.HTML)
        except Exception as e:  # noqa: BLE001
            log.warning("Hisobot yuborilmadi %s: %s", uid, e)


async def process(chat_id: int, message_id: int) -> str:
    """Bazadagi xabarni Gemini bilan o'qib, holatini qaytaradi: ok / not_shipment / failed."""
    d = await db.get_dispatch(chat_id, message_id)
    masked, _ = split_phone(d["raw_text"])
    try:
        res = await parse(masked)
    except Exception as e:  # noqa: BLE001
        log.warning("Gemini xato (%s/%s): %s", chat_id, message_id, e)
        await db.mark_failed(chat_id, message_id)
        return "failed"
    status = "ok" if res.is_shipment else "not_shipment"
    await db.save_parsed(chat_id, message_id, status, res.model_dump())
    return status


def short(d: dict | None) -> str:
    """Jo'natmaning bir qatorlik tavsifi."""
    if not d or d.get("status") != "ok":
        return "—"
    r = report.to_row(d)
    parts = [r.vehicle or "?", r.buyer or "?", f"{report.fmt(r.m3)} m³", f"{r.pallets} paddon"]
    if r.kley:
        parts.append(f"kley {r.kley} qop")
    if r.rodgips:
        parts.append(f"rodgips {r.rodgips} qop")
    return ", ".join(escape(p) for p in parts)


async def resend_day_if_closed(bot, d: date | None, reason: str):
    """Hisoboti allaqachon yuborilgan kun o'zgarsa — yangilangan hisobotni yuborish."""
    if d and d <= last_closed_day():
        s = await report.collect(d, d, report.day_title(d))
        await send_summary(bot, s, await db.subscribers(), f"♻️ <b>Yangilangan hisobot</b> ({reason})")


# ================= shaxsiy chat =================

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    if update.effective_chat.type != "private":
        return
    if not await db.has_admin():
        await db.add_user(u.id, u.full_name, "admin")
        await update.message.reply_text(
            "👑 Siz botning <b>admini</b> bo‘ldingiz va hisobotlarga obuna qilindingiz.\n\n"
            "Keyingi qadam: botni logistika guruhiga qo‘shing va guruhda /guruh yozing.\n\n"
            + HELP + ADMIN_HELP, parse_mode=ParseMode.HTML)
        return
    if await is_member(u.id):
        await db.add_user(u.id, u.full_name)
        extra = ADMIN_HELP if await is_admin(u.id) else ""
        await update.message.reply_text(HELP + extra, parse_mode=ParseMode.HTML)
        return
    await update.message.reply_text(
        f"Bu bot yopiq. So‘rovingiz adminga yuborildi.\nSizning ID: <code>{u.id}</code>",
        parse_mode=ParseMode.HTML)
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Ruxsat berish", callback_data=f"allow:{u.id}"),
        InlineKeyboardButton("❌ Rad etish", callback_data=f"deny:{u.id}"),
    ]])
    name = escape(u.full_name) + (f" (@{escape(u.username)})" if u.username else "")
    await notify(ctx.bot, await db.admins(),
                 f"🔔 <b>{name}</b> botdan foydalanmoqchi. ID: <code>{u.id}</code>", reply_markup=kb)


async def on_access_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not await is_admin(q.from_user.id):
        await q.answer("Faqat admin")
        return
    action, uid = q.data.split(":")
    uid = int(uid)
    if action == "allow":
        await db.add_user(uid, None)
        await q.edit_message_text(f"✅ Ruxsat berildi: <code>{uid}</code>", parse_mode=ParseMode.HTML)
        await notify(ctx.bot, [uid], "✅ Ruxsat berildi. Hisobotlar shu chatga keladi.\n\n" + HELP)
    else:
        await q.edit_message_text(f"❌ Rad etildi: <code>{uid}</code>", parse_mode=ParseMode.HTML)
    await q.answer()


def member_only(fn):
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not await is_member(update.effective_user.id):
            await update.effective_message.reply_text("Ruxsat yo‘q. Avval /start yozing.")
            return
        return await fn(update, ctx)
    return wrapper


def admin_only(fn):
    async def wrapper(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not await is_admin(update.effective_user.id):
            await update.effective_message.reply_text("Bu buyruq faqat admin uchun.")
            return
        return await fn(update, ctx)
    return wrapper


async def _reply_summary(update: Update, d1: date, d2: date, title: str):
    wait = await update.message.reply_text("⏳ Tayyorlanmoqda...")
    for d in await db.unread_between(d1, d2):
        await process(d["chat_id"], d["message_id"])
    s = await report.collect(d1, d2, title)
    await wait.delete()
    await send_summary(update.get_bot(), s, [update.effective_chat.id])


@member_only
async def cmd_hisobot(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if ctx.args:
        try:
            d = datetime.strptime(ctx.args[0], "%d.%m.%Y").date()
        except ValueError:
            await update.message.reply_text("Sana formati: /hisobot 09.11.2026")
            return
    else:
        d = last_closed_day()
    await _reply_summary(update, d, d, report.day_title(d))


@member_only
async def cmd_bugun(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    d = last_closed_day() + timedelta(days=1)
    await _reply_summary(update, d, d, report.day_title(d) + " (hozirgacha)")


def last_week() -> tuple[date, date]:
    end = last_closed_day()
    end -= timedelta(days=(end.weekday() + 1) % 7)       # oxirgi yakshanba
    return end - timedelta(days=6), end


def last_half() -> tuple[date, date]:
    """Oxirgi to'liq yopilgan yarim oy: 1–15 yoki 16–oy oxiri."""
    d = last_closed_day()
    if d.day == calendar.monthrange(d.year, d.month)[1]:
        return d.replace(day=16), d
    if d.day >= 15:
        return d.replace(day=1), d.replace(day=15)
    prev = d.replace(day=1) - timedelta(days=1)
    return prev.replace(day=16), prev


def last_month() -> tuple[date, date]:
    d = last_closed_day()
    if d.day == calendar.monthrange(d.year, d.month)[1]:
        return d.replace(day=1), d
    prev = d.replace(day=1) - timedelta(days=1)
    return prev.replace(day=1), prev


@member_only
async def cmd_hafta(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    d1, d2 = last_week()
    await _reply_summary(update, d1, d2, report.period_title(d1, d2, "Haftalik chiqim"))


@member_only
async def cmd_yarim_oy(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    d1, d2 = last_half()
    await _reply_summary(update, d1, d2, report.period_title(d1, d2, "15 kunlik chiqim"))


@member_only
async def cmd_oy(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    d1, d2 = last_month()
    await _reply_summary(update, d1, d2, f"{report.OYLAR[d1.month].capitalize()} {d1.year} — oylik chiqim")


@member_only
async def cmd_obuna(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = await db.get_user(update.effective_user.id)
    if u is None:
        await db.add_user(update.effective_user.id, update.effective_user.full_name)
        on = True
    else:
        on = not u["subscribed"]
        await db.set_subscribed(u["user_id"], on)
    await update.message.reply_text("🔔 Avtomatik hisobotlar yoqildi." if on
                                    else "🔕 Avtomatik hisobotlar o‘chirildi.")


@admin_only
async def cmd_users(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    rows = await db.list_users()
    out = [f"{'👑' if r['role'] == 'admin' else '👤'} <code>{r['user_id']}</code> "
           f"{escape(r['name'] or '')} {'🔔' if r['subscribed'] else '🔕'}" for r in rows]
    await update.message.reply_text("\n".join(out) or "Bo‘sh", parse_mode=ParseMode.HTML)


@admin_only
async def cmd_remove(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args or not ctx.args[0].lstrip("-").isdigit():
        await update.message.reply_text("Format: /olib_tashlash ID")
        return
    ok = await db.remove_user(int(ctx.args[0]))
    await update.message.reply_text("O‘chirildi" if ok else "Topilmadi (yoki admin)")


@admin_only
async def cmd_holat(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    gid = await db.get_setting("logistics_chat_id")
    retry = await db.to_retry(MAX_ATTEMPTS)
    await update.message.reply_text(
        f"Logistika guruhi: {'✅ ' + gid if gid else '❌ belgilanmagan (guruhda /guruh)'}\n"
        f"Hisobot oluvchilar: {len(await db.subscribers())}\n"
        f"O‘qilishi kutilayotgan xabarlar: {len(retry)}\n"
        f"Joriy ish kuni: {last_closed_day() + timedelta(days=1):%d.%m.%Y}\n"
        f"Gemini modeli: {cfg.gemini_model}")


# ================= guruh =================

async def cmd_guruh(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        await update.message.reply_text("Bu buyruqni logistika guruhining o‘zida yozing.")
        return
    if not await is_member(update.effective_user.id):
        await update.message.reply_text("Faqat bot foydalanuvchisi guruhni ulay oladi.")
        return
    await db.set_setting("logistics_chat_id", str(chat.id))
    await update.message.reply_text(
        "✅ Guruh ulandi. Jo‘natma xabarlari hisobotga kiradi — bot o‘qigan xabarga 👍 qo‘yadi.\n"
        "Noto‘g‘ri xabarni hisobotdan chiqarish: unga javob (reply) qilib /bekor yozing.")
    await notify(ctx.bot, await db.admins(), f"✅ Logistika guruhi ulandi: <b>{escape(chat.title or '')}</b>")


async def is_logistics(chat_id: int) -> bool:
    return str(chat_id) == await db.get_setting("logistics_chat_id")


async def react(bot, chat_id, message_id, emoji="👍"):
    try:
        await bot.set_message_reaction(chat_id, message_id, emoji)
    except Exception:  # noqa: BLE001 — reaksiya muhim emas
        pass


async def on_group_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if msg is None or not await is_logistics(msg.chat_id):
        return
    text = (msg.text or msg.caption or "").strip()
    if len(text) < 8:
        return  # izohsiz rasm, "ok", "+", stiker — hisobga olinmaydi
    is_edit = update.edited_message is not None
    old = await db.get_dispatch(msg.chat_id, msg.message_id) if is_edit else None
    if is_edit and old and old["raw_text"] == text:
        return
    _, phone = split_phone(text)
    wd = work_day(msg.date)
    await db.upsert_raw(msg.chat_id, msg.message_id, msg.date, wd, text, phone)
    status = await process(msg.chat_id, msg.message_id)
    new = await db.get_dispatch(msg.chat_id, msg.message_id)

    if status == "ok":
        await react(ctx.bot, msg.chat_id, msg.message_id)
        if wd is None:
            await notify(ctx.bot, await db.admins(),
                         f"⚠️ Jo‘natma 07:00–08:00 oralig‘ida yozildi — hech bir kunlik hisobotga "
                         f"kirmaydi:\n{short(new)}")

    if is_edit and old:
        link = report._link(msg.chat_id, msg.message_id)
        await notify(ctx.bot, await db.subscribers(),
                     f"✏️ <b>Omborchi xabarni o‘zgartirdi</b> "
                     f"({msg.date.astimezone(TZ):%d.%m %H:%M})\n"
                     f"Avval: {short(old)}\nEndi: {short(new)}"
                     + (f"\n<a href=\"{link}\">Xabarni ochish</a>" if link else ""))
        if status != "failed":
            await resend_day_if_closed(ctx.bot, wd, "xabar o‘zgartirildi")


async def cmd_bekor(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not await is_logistics(msg.chat_id):
        return
    target = msg.reply_to_message
    if not target:
        await msg.reply_text("Hisobotdan chiqariladigan xabarga javob (reply) qilib /bekor yozing.")
        return
    d = await db.get_dispatch(msg.chat_id, target.message_id)
    if not d or d["status"] == "cancelled":
        await msg.reply_text("Bu xabar hisobotda yo‘q.")
        return
    await db.set_status(msg.chat_id, target.message_id, "cancelled")
    await react(ctx.bot, msg.chat_id, target.message_id, "👎")
    await msg.reply_text("❌ Hisobotdan chiqarildi.")
    who = escape(update.effective_user.full_name)
    await notify(ctx.bot, await db.subscribers(),
                 f"🗑 <b>{who}</b> jo‘natmani bekor qildi "
                 f"({d['sent_at'].astimezone(TZ):%d.%m %H:%M}):\n{short(d)}")
    await resend_day_if_closed(ctx.bot, d["work_day"], "jo‘natma bekor qilindi")


# ================= jadval =================

async def job_retry(ctx: ContextTypes.DEFAULT_TYPE):
    """Gemini avval o'qiy olmagan xabarlarni qayta o'qish (har 10 daqiqa)."""
    for d in await db.to_retry(MAX_ATTEMPTS):
        status = await process(d["chat_id"], d["message_id"])
        if status == "ok":
            await react(ctx.bot, d["chat_id"], d["message_id"])
            await resend_day_if_closed(ctx.bot, d["work_day"], "kechikib o‘qilgan xabar")
        elif status == "failed" and d["attempts"] + 1 >= MAX_ATTEMPTS:
            link = report._link(d["chat_id"], d["message_id"])
            await notify(ctx.bot, await db.admins(),
                         "⚠️ Gemini bu xabarni 2 soat davomida o‘qiy olmadi — hisobotga kirmaydi:\n"
                         f"<i>{escape((d['raw_text'] or '')[:300])}</i>"
                         + (f"\n<a href=\"{link}\">Xabar</a>" if link else ""))


def due_reports(today: date) -> list[tuple[date, date, str]]:
    """Bugun 07:00 da yuboriladigan hisobotlar ro'yxati."""
    d = last_closed_day()
    out = [(d, d, report.day_title(d))]
    if today.weekday() == 0:
        w1, w2 = last_week()
        out.append((w1, w2, report.period_title(w1, w2, "Haftalik chiqim")))
    if today.day in (1, 16):
        h1, h2 = last_half()
        out.append((h1, h2, report.period_title(h1, h2, "15 kunlik chiqim")))
    if today.day == 1:
        m1, m2 = last_month()
        out.append((m1, m2, f"{report.OYLAR[m1.month].capitalize()} {m1.year} — oylik chiqim"))
    return out


async def job_reports(ctx: ContextTypes.DEFAULT_TYPE):
    """Har kuni 07:00: kunlik; dushanba — haftalik; 16 va 1 — 15 kunlik; 1 — oylik."""
    subs = await db.subscribers()
    if not subs:
        log.warning("Hisobot oluvchi yo'q")
        return
    for d1, d2, title in due_reports(datetime.now(TZ).date()):
        for d in await db.unread_between(d1, d2):
            await process(d["chat_id"], d["message_id"])
        await send_summary(ctx.bot, await report.collect(d1, d2, title), subs)
    if not await db.get_setting("logistics_chat_id"):
        await notify(ctx.bot, await db.admins(),
                     "⚠️ Logistika guruhi ulanmagan — botni guruhga qo‘shib, /guruh yozing.")


# ================= ishga tushirish =================

_last_alert = 0.0


async def on_error(update: object, ctx: ContextTypes.DEFAULT_TYPE):
    global _last_alert
    if isinstance(ctx.error, Conflict):
        log.error("Bot boshqa joyda ham ishlayapti — faqat bitta nusxani qoldiring.")
        return
    log.error("Xato: %s", ctx.error, exc_info=ctx.error)
    if _time.time() - _last_alert > 600:          # 10 daqiqada ko'pi bilan bitta ogohlantirish
        _last_alert = _time.time()
        await notify(ctx.bot, await db.admins(), f"⚠️ Botda xato: <code>{escape(str(ctx.error))[:300]}</code>")


async def post_init(app: Application):
    await db.init()
    await app.bot.set_my_commands([
        ("hisobot", "Oxirgi kunlik hisobot"), ("bugun", "Joriy kun (hozirgacha)"),
        ("hafta", "O‘tgan hafta"), ("yarim_oy", "Oxirgi 15 kun"), ("oy", "O‘tgan oy"),
        ("obuna", "Avtomatik hisobotni yoqish/o‘chirish"), ("start", "Yordam"),
    ])


def _single_instance():
    """Bir kompyuterda ikkinchi nusxani ishga tushirmaslik."""
    import fcntl
    lock = open(Path(__file__).with_name(".bot.lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("Bot allaqachon boshqa oynada ishlayapti.")
    return lock


def main():
    if not cfg.telegram_token:
        raise SystemExit("TELEGRAM_BOT_TOKEN berilmagan")
    _lock = _single_instance()  # noqa: F841
    app = (Application.builder().token(cfg.telegram_token).post_init(post_init)
           .concurrent_updates(4).build())
    private = filters.ChatType.PRIVATE
    groups = filters.ChatType.GROUPS

    app.add_handler(CommandHandler(["start", "help"], cmd_start))
    app.add_handler(CommandHandler("hisobot", cmd_hisobot, private))
    app.add_handler(CommandHandler("bugun", cmd_bugun, private))
    app.add_handler(CommandHandler("hafta", cmd_hafta, private))
    app.add_handler(CommandHandler("yarim_oy", cmd_yarim_oy, private))
    app.add_handler(CommandHandler("oy", cmd_oy, private))
    app.add_handler(CommandHandler("obuna", cmd_obuna, private))
    app.add_handler(CommandHandler("foydalanuvchilar", cmd_users, private))
    app.add_handler(CommandHandler("olib_tashlash", cmd_remove, private))
    app.add_handler(CommandHandler("holat", cmd_holat, private))
    app.add_handler(CommandHandler("guruh", cmd_guruh))
    app.add_handler(CommandHandler("bekor", cmd_bekor, groups))
    app.add_handler(CallbackQueryHandler(on_access_button, pattern=r"^(allow|deny):"))
    app.add_handler(MessageHandler(groups & ~filters.COMMAND, on_group_message))
    app.add_error_handler(on_error)

    app.job_queue.run_daily(job_reports, time=dtime(cfg.day_end_hour, 0, tzinfo=TZ), name="hisobotlar")
    app.job_queue.run_repeating(job_retry, interval=600, first=60, name="qayta_oqish")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
