#!/bin/zsh
# Bir martalik o'rnatish. Homebrew bo'lmasa avval uni o'rnating (README'ga qarang).
set -e
cd "$(dirname "$0")"

if ! command -v brew >/dev/null; then
  echo "Homebrew topilmadi. README.md dagi 1-qadamni bajaring."; exit 1
fi
eval "$(brew shellenv)"

brew list python@3.12   >/dev/null 2>&1 || brew install python@3.12
brew list postgresql@16 >/dev/null 2>&1 || brew install postgresql@16
brew services start postgresql@16
export PATH="$(brew --prefix postgresql@16)/bin:$PATH"

# PostgreSQL ishga tushishini kutish
for i in {1..15}; do pg_isready -q && break; sleep 1; done
createdb nakladnoy 2>/dev/null || echo "Baza 'nakladnoy' allaqachon bor"

"$(brew --prefix python@3.12)/bin/python3.12" -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

[ -f .env ] || cp .env.example .env
echo
echo "✅ Tayyor. Endi .env faylga TELEGRAM_BOT_TOKEN va GEMINI_API_KEY ni yozing, keyin:"
echo "   ./run.sh"
