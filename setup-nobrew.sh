#!/bin/zsh
# Homebrew'siz o'rnatish: python.org Python + Postgres.app ishlatiladi.
set -e
cd "$(dirname "$0")"

PY=$(ls /Library/Frameworks/Python.framework/Versions/3.1*/bin/python3 2>/dev/null | tail -1)
if [ -z "$PY" ]; then
  echo "Python topilmadi. https://www.python.org/downloads/macos/ dan o'rnating."; exit 1
fi

PGBIN=/Applications/Postgres.app/Contents/Versions/latest/bin
if [ ! -x "$PGBIN/psql" ]; then
  echo "Postgres.app topilmadi. https://postgresapp.com dan o'rnatib, 'Initialize' bosing."; exit 1
fi
if ! "$PGBIN/pg_isready" -q; then
  echo "PostgreSQL ishlamayapti. Postgres.app ni ochib 'Start' bosing."; exit 1
fi
"$PGBIN/createdb" nakladnoy 2>/dev/null || echo "Baza 'nakladnoy' allaqachon bor"

"$PY" -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

[ -f .env ] || cp .env.example .env
echo
echo "✅ Tayyor. Ishga tushirish: ./run.sh"
