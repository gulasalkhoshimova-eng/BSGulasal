-- Logistika guruhidagi jo'natma xabarlari (1 xabar = 1 mashina)
CREATE TABLE IF NOT EXISTS dispatches (
    chat_id      BIGINT NOT NULL,
    message_id   BIGINT NOT NULL,
    sent_at      TIMESTAMPTZ NOT NULL,
    work_day     DATE,                 -- ish kuni (08:00->07:00); 07:00-08:00 oralig'i = NULL
    raw_text     TEXT,
    phone        TEXT,                 -- matndan lokal ajratiladi, Gemini'ga yuborilmaydi
    status       TEXT NOT NULL DEFAULT 'pending',
                 -- pending | ok | not_shipment | cancelled | failed
    parsed       JSONB,
    attempts     INT NOT NULL DEFAULT 0,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (chat_id, message_id)
);
CREATE INDEX IF NOT EXISTS dispatches_day_idx ON dispatches(work_day);

-- Bot foydalanuvchilari (hisobot oluvchilar)
CREATE TABLE IF NOT EXISTS users (
    user_id     BIGINT PRIMARY KEY,
    name        TEXT,
    role        TEXT NOT NULL DEFAULT 'user',   -- admin | user
    subscribed  BOOLEAN NOT NULL DEFAULT true,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Sozlamalar (logistika guruhi ID va h.k.)
CREATE TABLE IF NOT EXISTS settings (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);
