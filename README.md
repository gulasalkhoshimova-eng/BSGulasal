# Nakladnoy → 1C CSV Telegram bot

Qo‘lda yozilgan yuk xati (накладная) rasmini Telegram’ga yuborasiz → Gemini o‘qiydi →
siz tekshirib tasdiqlaysiz → kun oxirida `/csv` bilan bitta CSV olib 1C’ga yuklaysiz.

## O‘rnatish (bir marta)

1. **Homebrew** (parol so‘raydi, shuning uchun o‘zingiz ishga tushirasiz):
   ```bash
   /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
   ```
   Oxirida ko‘rsatilgan `eval "$(/opt/homebrew/bin/brew shellenv)"` qatorini ham bajaring.
2. **Python + PostgreSQL + kutubxonalar:**
   ```bash
   cd ~/Projects/nakladnoy-bot && chmod +x setup.sh run.sh && ./setup.sh
   ```
3. **Kalitlar** — `.env` faylini oching va to‘ldiring:
   - `TELEGRAM_BOT_TOKEN` — Telegram’da @BotFather → /newbot
   - `GEMINI_API_KEY` — https://aistudio.google.com/apikey
4. Ishga tushirish: `./run.sh`. Botga `/start` yozing — u sizning ID’ingizni ko‘rsatadi.
   Shu ID’ni `.env` dagi `ALLOWED_USER_IDS=` ga yozing va botni qayta ishga tushiring
   (aks holda botdan har kim foydalana oladi).

## Kundalik ish

1. Kontragentlarni bir marta kiriting (1C’dagi nom bilan aynan bir xil):
   `/kontragent Nomi | INN | Договор № ... от ... | nakladnoyda yozilishi1, yozilishi2`
2. Nakladnoy rasmini yuboring → tekshiring → ✅ yoki ✏️.
3. `/csv` → fayl → 1C’ga yuklash.

Bot qatorlarni tekshiradi: `soni × narx ≠ summa` yoki `Итого ≠ yig‘indi` bo‘lsa ogohlantiradi.

## CSV formati

`;` bilan ajratilgan, UTF-8 (BOM bilan), har tovar = bitta qator. Ustunlar
`export_csv.py` dagi `COLUMNS` ro‘yxatida — 1C’dagi yuklash обработкаsi boshqa nom
yoki tartib kutsa, faqat shu ro‘yxatni va `build_csv` dagi qatorni moslang.

## Railway’ga deploy

1. railway.com → **New Project → Deploy from GitHub repo** → shu repozitoriy.
2. Shu loyihada **+ New → Database → PostgreSQL** qo‘shing.
3. Bot servisi → **Variables**:
   - `TELEGRAM_BOT_TOKEN`, `GEMINI_API_KEY`, `ALLOWED_USER_IDS`
   - `GEMINI_MODEL=gemini-3.8-flash`
   - `DATABASE_URL` → **Add Reference** → `Postgres.DATABASE_URL`
4. Deploy. Loglarda `Application started` chiqsa — tayyor.

⚠️ Railway’da ishga tushgach, kompyuterdagi botni to‘xtating (Ctrl+C) — bitta token
bilan ikki joyda ishlasa Telegram `Conflict` xatosini beradi.

## Fayllar

| Fayl | Vazifa |
|---|---|
| `bot.py` | Telegram buyruqlari, tekshirish, tuzatish |
| `ocr.py` | Gemini’ga rasm yuborish, JSON sxema |
| `db.py`, `schema.sql` | PostgreSQL |
| `export_csv.py` | 1C uchun CSV |

## Logistika: kunlik chiqim hisoboti

Bot logistika guruhidagi "mashina chiqdi" xabarlarini o‘qiydi (Gemini) va har kuni
**07:00** da kechagi 08:00 → bugungi 07:00 bo‘yicha hisobot yuboradi:
o‘lchamlar bo‘yicha m³ va jami, kley (qop), paddon, xaridorlar bo‘yicha, manzillar.

Sozlash (bir marta):
1. @BotFather → `/setprivacy` → bot → **Disable** (aks holda bot guruh xabarlarini ko‘rmaydi).
2. Botni logistika guruhiga qo‘shing va guruhda `/guruh` yozing.
3. Botning shaxsiy chatida `/hisobot_menga` yozing.
4. Istalgan payt: `/hisobot` yoki `/hisobot 09.11.2026`.

m³: xabarda "kub/m3" bo‘lsa — shu olinadi; faqat dona bo‘lsa — o‘lcham (mm) × dona.
