"""Nakladnoy rasmi -> Gemini -> PostgreSQL -> 1C uchun CSV. Telegram bot."""
import logging
import re
from datetime import date, datetime
from datetime import time as dtime
from decimal import Decimal, InvalidOperation
from html import escape
from pathlib import Path

from telegram.error import Conflict

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters,
)

import db
from config import cfg
from export_csv import build_csv
from ocr import read_invoice
import logistics

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("bot")

HELP = """<b>Qanday ishlaydi</b>
1. Nakladnoy rasmini yuboring (bir nechtasini ketma-ket ham bo‘ladi).
2. Bot o‘qib, tekshirish uchun ko‘rsatadi → <b>✅ Tasdiqlash</b> yoki <b>✏️ Tuzatish</b>.
3. Kun oxirida /csv — tasdiqlangan barcha nakladnoylar bitta CSV faylda.

<b>Buyruqlar</b>
/csv — tasdiqlangan, hali yuklanmagan nakladnoylar
/csv 03.11.2026 — faqat shu kuni yuborilganlar
/kontragent Nomi | INN | Договор | taxallus1, taxallus2
/kontragentlar — ro‘yxat (ID bilan)
/kontragent_ochir ID

<b>Tuzatish rejimida</b> (har biri alohida qator):
<code>3; Офис папка; шт; 2; 40000</code> — 3-qatorni almashtirish/qo‘shish
<code>-3</code> — 3-qatorni o‘chirish
<code>sana: 03.11.2026</code>
<code>k: 5</code> — kontragent ID (/kontragentlar dan)
<code>izoh: matn</code>
<code>tayyor</code> — tuzatishni tugatish

<b>Logistika hisoboti</b>
/guruh — (logistika guruhida yozing) shu guruhni kuzatish
/hisobot_menga — kunlik hisobot shu chatga kelsin (har kuni 07:00)
/hisobot — oxirgi kunlik hisobot (kecha 08:00 → bugun 07:00)
/hisobot 09.11.2026 — shu kuni 07:00 da tugagan davr"""


def allowed(update: Update) -> bool:
    return not cfg.allowed_users or update.effective_user.id in cfg.allowed_users


def money(x) -> str:
    v = Decimal(str(x))
    s = f"{v:,.2f}".replace(",", " ")
    return s[:-3] if s.endswith(".00") else s


def qty_str(x) -> str:
    return format(Decimal(str(x)).normalize(), "f")


def preview(inv: dict) -> str:
    lines = [f"<b>Nakladnoy #{inv['id']}</b>  ({escape(inv['status'])})"]
    dt = inv["doc_date"].strftime("%d.%m.%Y") if inv["doc_date"] else "❓"
    lines.append(f"Sana: <b>{dt}</b>")
    lines.append(f"Kimdan (rasmda): {escape(inv['supplier_raw'] or '')}")
    if inv.get("k_name"):
        lines.append(f"1C kontragent: <b>{escape(inv['k_name'])}</b>")
        lines.append(f"Договор: {escape(inv['k_contract'] or '—')}")
    else:
        lines.append("⚠️ <b>Kontragent topilmadi</b> — ✏️ orqali <code>k: ID</code> kiriting")
    if inv.get("comment"):
        lines.append(f"Izoh: {escape(inv['comment'])}")
    lines.append("")
    warn = []
    for it in inv["items"]:
        lines.append(
            f"{it['line_no']}. {escape(it['name'])} — {qty_str(it['qty'])} {escape(it['unit'] or '')}"
            f" × {money(it['price'])} = <b>{money(it['amount'])}</b>"
        )
        calc = Decimal(str(it["qty"])) * Decimal(str(it["price"]))
        if abs(calc - Decimal(str(it["amount"]))) > 1:
            warn.append(f"{it['line_no']}-qator: {qty_str(it['qty'])}×{money(it['price'])}={money(calc)}, "
                        f"lekin yozilgan {money(it['amount'])}")
    s = db.items_sum(inv["items"])
    lines.append(f"\nJami: <b>{money(s)}</b> so‘m")
    if inv["total"] is not None and abs(s - Decimal(str(inv["total"]))) > 1:
        warn.append(f"Rasmdagi «Итого» {money(inv['total'])}, qatorlar yig‘indisi {money(s)}")
    if warn:
        lines.append("\n⚠️ <b>Tekshiring:</b>\n" + "\n".join("• " + escape(w) for w in warn))
    return "\n".join(lines)


def buttons(inv_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Tasdiqlash", callback_data=f"ok:{inv_id}"),
        InlineKeyboardButton("✏️ Tuzatish", callback_data=f"edit:{inv_id}"),
        InlineKeyboardButton("❌ Bekor", callback_data=f"no:{inv_id}"),
    ]])


async def show(target, inv_id: int):
    inv = await db.get_invoice(inv_id)
    await target.reply_text(preview(inv), parse_mode=ParseMode.HTML, reply_markup=buttons(inv_id))


# ---------------- handlers ----------------

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not allowed(update):
        await update.message.reply_text(f"Ruxsat yo‘q. Sizning ID: {uid}")
        return
    await update.message.reply_text(f"Sizning Telegram ID: <code>{uid}</code>\n\n{HELP}",
                                    parse_mode=ParseMode.HTML)


async def on_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    msg = update.message
    if msg.photo:
        tg_file, mime = await msg.photo[-1].get_file(), "image/jpeg"
    else:
        tg_file, mime = await msg.document.get_file(), msg.document.mime_type or "image/jpeg"
    wait = await msg.reply_text("⏳ O‘qilmoqda...")
    try:
        data = bytes(await tg_file.download_as_bytearray())
        parsed = await read_invoice(data, mime)
    except Exception as e:  # noqa: BLE001
        log.exception("OCR xato")
        await wait.edit_text(f"❌ O‘qib bo‘lmadi: {e}\nRasmni aniqroq qilib qayta yuboring.")
        return
    if not parsed.items:
        await wait.edit_text("❌ Rasmda tovar qatorlari topilmadi.")
        return
    raw = parsed.model_dump()
    try:
        raw_date = date.fromisoformat(parsed.date)
    except ValueError:
        raw_date = None
    k = await db.match_contractor(parsed.supplier)
    inv_id = await db.save_invoice(
        update.effective_user.id,
        {"date": raw_date, "number": parsed.number, "supplier": parsed.supplier,
         "buyer": parsed.buyer, "total": parsed.total,
         "items": [i.model_dump() for i in parsed.items]},
        k["id"] if k else None, tg_file.file_id, raw,
    )
    await wait.delete()
    await show(msg, inv_id)


async def on_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not allowed(update):
        await q.answer("Ruxsat yo‘q")
        return
    action, inv_id = q.data.split(":")
    inv_id = int(inv_id)
    inv = await db.get_invoice(inv_id)
    if not inv or inv["user_id"] != update.effective_user.id:
        await q.answer("Topilmadi")
        return
    if inv["status"] == "exported":
        await q.answer("Bu nakladnoy allaqachon CSV’ga chiqarilgan")
        return
    if action == "ok":
        if not inv.get("k_name"):
            await q.answer("Avval kontragentni belgilang (✏️ → k: ID)", show_alert=True)
            return
        await db.update_invoice(inv_id, status="confirmed")
        ctx.user_data.pop("editing", None)
        await q.answer("Tasdiqlandi")
        await q.edit_message_text(preview(await db.get_invoice(inv_id)), parse_mode=ParseMode.HTML)
    elif action == "no":
        await db.update_invoice(inv_id, status="cancelled")
        ctx.user_data.pop("editing", None)
        await q.answer("Bekor qilindi")
        await q.edit_message_text(f"❌ Nakladnoy #{inv_id} bekor qilindi.")
    elif action == "edit":
        await db.update_invoice(inv_id, status="draft")
        ctx.user_data["editing"] = inv_id
        await q.answer()
        await q.message.reply_text(
            f"✏️ #{inv_id} tuzatish rejimi. Tuzatishlarni yuboring, oxirida <code>tayyor</code>.\n"
            "Format: <code>3; Офис папка; шт; 2; 40000</code>, <code>-3</code>, "
            "<code>sana: 03.11.2026</code>, <code>k: 5</code>, <code>izoh: ...</code>",
            parse_mode=ParseMode.HTML,
        )


def _dec(s: str) -> Decimal:
    """'250.000' / '250 000' -> 250000, '2,5' / '2.5' -> 2.5"""
    s = s.strip().replace(" ", "")
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", s):
        s = s.replace(".", "")
    return Decimal(s)


async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    inv_id = ctx.user_data.get("editing")
    if not inv_id:
        await update.message.reply_text("Nakladnoy rasmini yuboring yoki /start ni bosing.")
        return
    inv = await db.get_invoice(inv_id)
    items = {i["line_no"]: dict(i) for i in inv["items"]}
    errors, changed = [], False
    for line in update.message.text.splitlines():
        line = line.strip()
        low = line.lower()
        if not line:
            continue
        try:
            if low == "tayyor":
                ctx.user_data.pop("editing", None)
            elif low.startswith("sana:"):
                await db.update_invoice(
                    inv_id, doc_date=datetime.strptime(line[5:].strip(), "%d.%m.%Y").date())
            elif low.startswith("k:"):
                cid = int(line[2:].strip())
                if not any(c["id"] == cid for c in await db.list_contractors()):
                    raise ValueError(f"kontragent ID {cid} yo‘q")
                await db.update_invoice(inv_id, contractor_id=cid)
            elif low.startswith("izoh:"):
                await db.update_invoice(inv_id, comment=line[5:].strip() or None)
            elif line.startswith("-") and line[1:].strip().isdigit():
                items.pop(int(line[1:]), None)
                changed = True
            else:
                p = [x.strip() for x in line.split(";")]
                if len(p) != 5:
                    raise ValueError("5 ta qism kerak: N; nomi; birlik; soni; narxi")
                qty, price = _dec(p[3]), _dec(p[4])
                items[int(p[0])] = {"name": p[1], "unit": p[2], "qty": qty, "price": price,
                                    "amount": qty * price}
                changed = True
        except (ValueError, InvalidOperation) as e:
            errors.append(f"«{line}»: {e}")
    if changed:
        await db.replace_items(inv_id, [items[k] for k in sorted(items)])
    if errors:
        await update.message.reply_text("⚠️ Tushunmadim:\n" + "\n".join(errors))
    await show(update.message, inv_id)


async def cmd_csv(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    day = None
    if ctx.args:
        try:
            day = datetime.strptime(ctx.args[0], "%d.%m.%Y").date()
        except ValueError:
            await update.message.reply_text("Sana formati: /csv 03.11.2026")
            return
    invs = await db.pending_export(update.effective_user.id, day)
    if not invs:
        await update.message.reply_text("Tasdiqlangan, yuklanmagan nakladnoy yo‘q.")
        return
    data = build_csv(invs)
    name = f"postuplenie_{datetime.now():%Y%m%d_%H%M}.csv"
    total = sum(db.items_sum(i["items"]) for i in invs)
    await update.message.reply_document(
        InputFile(data, filename=name),
        caption=f"{len(invs)} ta nakladnoy, jami {money(total)} so‘m",
    )
    await db.mark_exported([i["id"] for i in invs])


async def cmd_add_k(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    text = update.message.text.partition(" ")[2]
    p = [x.strip() for x in text.split("|")]
    if not p or not p[0]:
        await update.message.reply_text(
            "Format: /kontragent Nomi | INN | Договор | taxallus1, taxallus2")
        return
    p += [""] * (4 - len(p))
    aliases = [a.strip() for a in p[3].split(",") if a.strip()]
    await db.add_contractor(p[0], p[1] or None, p[2] or None, aliases)
    await update.message.reply_text(f"✅ Saqlandi: {p[0]}")


async def cmd_list_k(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    rows = await db.list_contractors()
    if not rows:
        await update.message.reply_text("Hali kontragent yo‘q. /kontragent bilan qo‘shing.")
        return
    out = [f"<code>{r['id']}</code> {escape(r['name'])} | {escape(r['inn'] or '')} | "
           f"{escape(r['contract'] or '')}" for r in rows]
    await update.message.reply_text("\n".join(out), parse_mode=ParseMode.HTML)


async def cmd_del_k(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    if not ctx.args or not ctx.args[0].isdigit():
        await update.message.reply_text("Format: /kontragent_ochir ID")
        return
    ok = await db.delete_contractor(int(ctx.args[0]))
    await update.message.reply_text("O‘chirildi" if ok else "Topilmadi")


async def post_init(app: Application):
    await db.init()


# ---------------- logistika ----------------

async def cmd_guruh(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Logistika guruhida yoziladi: bot shu guruh xabarlarini kuzata boshlaydi."""
    if not allowed(update):
        return
    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        await update.message.reply_text("Bu buyruqni logistika guruhining o‘zida yozing.")
        return
    await db.set_setting("logistics_chat_id", str(chat.id))
    await update.message.reply_text(
        "✅ Shu guruh kuzatiladi. Mashina jo‘natilgani haqidagi xabarlar kunlik hisobotga kiradi.")


async def cmd_hisobot_menga(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    await db.set_setting("report_chat_id", str(update.effective_chat.id))
    await update.message.reply_text(
        f"✅ Kunlik hisobot shu chatga har kuni {cfg.report_end_hour:02d}:00 da keladi.")


async def cmd_hisobot(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not allowed(update):
        return
    if ctx.args:
        try:
            end_day = datetime.strptime(ctx.args[0], "%d.%m.%Y").date()
        except ValueError:
            await update.message.reply_text("Sana formati: /hisobot 09.11.2026")
            return
    else:
        end_day = logistics.last_window_end_day()
    wait = await update.message.reply_text("⏳ Hisobot tayyorlanmoqda...")
    text = await logistics.build_report(end_day)
    await wait.edit_text(text, parse_mode=ParseMode.HTML)


async def on_group_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if msg is None or str(msg.chat_id) != await db.get_setting("logistics_chat_id"):
        return
    text = msg.text or msg.caption or ""
    image = None
    if msg.photo:
        f = await msg.photo[-1].get_file()
        image = bytes(await f.download_as_bytearray())
    if not image and len(text.strip()) < 8:
        return  # "ok", "+", stiker va h.k.
    await logistics.handle_message(msg.chat_id, msg.message_id, msg.date, text, image)


async def daily_report_job(ctx: ContextTypes.DEFAULT_TYPE):
    chat_id = await db.get_setting("report_chat_id")
    if not chat_id:
        log.warning("Hisobot chati belgilanmagan — botga /hisobot_menga yozing")
        return
    text = await logistics.build_report(logistics.last_window_end_day())
    await ctx.bot.send_message(int(chat_id), text, parse_mode=ParseMode.HTML)


async def on_error(update: object, ctx: ContextTypes.DEFAULT_TYPE):
    if isinstance(ctx.error, Conflict):
        log.error("Bot boshqa joyda ham ishlayapti — faqat bitta oynada ./run.sh qoldiring.")
        return
    log.error("Xato: %s", ctx.error, exc_info=ctx.error)


def _single_instance():
    """Bir kompyuterda botning ikkinchi nusxasini ishga tushirmaslik."""
    import fcntl
    lock = open(Path(__file__).with_name(".bot.lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("Bot allaqachon boshqa terminal oynasida ishlayapti. "
                         "Ikkinchisini ochish shart emas.")
    return lock


def main():
    if not cfg.telegram_token:
        raise SystemExit("TELEGRAM_BOT_TOKEN .env faylida yo'q")
    _lock = _single_instance()  # noqa: F841 — jarayon tugaguncha ushlab turiladi
    app = Application.builder().token(cfg.telegram_token).post_init(post_init).build()
    app.add_handler(CommandHandler(["start", "help"], start))
    app.add_handler(CommandHandler("csv", cmd_csv))
    app.add_handler(CommandHandler("kontragent", cmd_add_k))
    app.add_handler(CommandHandler("kontragentlar", cmd_list_k))
    app.add_handler(CommandHandler("kontragent_ochir", cmd_del_k))
    app.add_handler(CommandHandler("guruh", cmd_guruh))
    app.add_handler(CommandHandler("hisobot_menga", cmd_hisobot_menga))
    app.add_handler(CommandHandler("hisobot", cmd_hisobot))
    # Guruh xabarlari (yangi va tahrirlangan) — logistika uchun
    app.add_handler(MessageHandler(filters.ChatType.GROUPS & ~filters.COMMAND, on_group_message))
    # Shaxsiy chat — nakladnoy
    private = filters.ChatType.PRIVATE
    app.add_handler(MessageHandler(private & (filters.PHOTO | filters.Document.IMAGE), on_photo))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(private & filters.TEXT & ~filters.COMMAND, on_text))
    app.add_error_handler(on_error)
    app.job_queue.run_daily(daily_report_job, time=dtime(cfg.report_end_hour, 0, tzinfo=logistics.TZ),
                            name="kunlik_hisobot")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
