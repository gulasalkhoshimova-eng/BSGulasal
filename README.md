# Logistika hisobot boti

Telegram’dagi logistika guruhida omborchi yozgan "mashina chiqdi" xabarlarini o‘qiydi
(Gemini), PostgreSQL’ga yozadi va Excel hisobot yuboradi.

| Hisobot | Qachon | Davr |
|---|---|---|
| Kunlik | har kuni 07:00 | kecha 08:00 → bugun 07:00 |
| Haftalik | dushanba 07:00 | dushanba–yakshanba |
| 15 kunlik | 16- va 1-sana 07:00 | 1–15 / 16–oy oxiri |
| Oylik | 1-sana 07:00 | o‘tgan oy |

Excel varaqlari: **Xulosa** (o‘lchamlar bo‘yicha m³, jami, paddon, kley, rodgips),
**Xaridorlar**, **Manzillar** (viloyat/tuman), **Mashinalar** (raqam, haydovchi, telefon,
xabarga havola), **O‘qilmagan** (Gemini o‘qiy olmagan xabarlar).

## Xabar namunasi (omborchi)

```
70 B 275 YA
Shafoatov Shahzod
+998946591422
200x300x625 ...40,5m³
18 paddon
Gazablok kley
40 qop
Xojakbar mega qurilish mchj
Surxondaryo denov
```

Telefon raqam Gemini’ga yuborilmaydi — lokal ajratiladi va faqat Excel’da ko‘rinadi.

## Sozlash (bir marta)

1. @BotFather → `/setprivacy` → bot → **Disable** (bot guruh xabarlarini ko‘rishi uchun).
2. Botga shaxsiy chatda `/start` — **birinchi yozgan odam admin** bo‘ladi.
3. Botni logistika guruhiga qo‘shing, guruhda `/guruh` yozing.
4. Rahbar va yordamchi botga `/start` yozadi → adminga "✅ Ruxsat berish" tugmasi keladi.

## Guruhda

- Bot o‘qigan jo‘natmaga 👍 qo‘yadi.
- Xabar **tahrirlansa** — hamma hisobot oluvchilarga "avval / endi" ogohlantirish keladi,
  hisobot yuborilgan kun bo‘lsa — yangilangan hisobot qayta yuboriladi.
- Noto‘g‘ri xabarga **reply** qilib `/bekor` — hisobotdan chiqariladi (Telegram botlarga
  o‘chirilgan xabar haqida xabar bermaydi, shuning uchun o‘chirish o‘rniga `/bekor`).
- 07:00–08:00 oralig‘idagi jo‘natma hech bir kunga kirmaydi — admin ogohlantiriladi.

## Buyruqlar (shaxsiy chat)

`/hisobot`, `/hisobot 09.11.2026`, `/bugun`, `/hafta`, `/yarim_oy`, `/oy`, `/obuna`;
admin: `/foydalanuvchilar`, `/olib_tashlash ID`, `/holat`.

## Ishga tushirish

Lokal: `./run.sh` (PostgreSQL ishlab turishi kerak).

Railway: GitHub repo’dan deploy + PostgreSQL. Variables:
`TELEGRAM_BOT_TOKEN`, `GEMINI_API_KEY`, `GEMINI_MODEL=gemini-3.8-flash`,
`DATABASE_URL=${{Postgres.DATABASE_URL}}`. Railway’da ishga tushgach lokal botni to‘xtating
(bitta token — bitta nusxa).

## Fayllar

| Fayl | Vazifa |
|---|---|
| `bot.py` | Telegram: guruh, buyruqlar, foydalanuvchilar, jadval |
| `parser.py` | Gemini bilan xabarni o‘qish, telefonni yashirish, ish kuni |
| `report.py` | Hisob-kitob, Excel, qisqa matn |
| `db.py`, `schema.sql` | PostgreSQL |
