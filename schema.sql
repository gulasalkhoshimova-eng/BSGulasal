CREATE TABLE IF NOT EXISTS contractors (
    id        SERIAL PRIMARY KEY,
    name      TEXT NOT NULL,          -- 1C'dagi aniq nom
    inn       TEXT,
    contract  TEXT,                   -- "Договор № ..." to'liq matni
    aliases   TEXT[] NOT NULL DEFAULT '{}',  -- nakladnoyda qanday yozilishi
    UNIQUE (name)
);

CREATE TABLE IF NOT EXISTS invoices (
    id             SERIAL PRIMARY KEY,
    user_id        BIGINT NOT NULL,
    doc_date       DATE,
    doc_number     TEXT,
    supplier_raw   TEXT,              -- nakladnoydan o'qilgan "Kimdan"
    buyer_raw      TEXT,              -- "Kimga"
    contractor_id  INT REFERENCES contractors(id),
    total          NUMERIC(18,2),     -- nakladnoydagi "Итого"
    comment        TEXT,
    status         TEXT NOT NULL DEFAULT 'draft',  -- draft | confirmed | exported | cancelled
    photo_file_id  TEXT,
    raw_json       JSONB,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    exported_at    TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS invoice_items (
    id          SERIAL PRIMARY KEY,
    invoice_id  INT NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    line_no     INT NOT NULL,
    name        TEXT NOT NULL,
    unit        TEXT,
    qty         NUMERIC(18,3) NOT NULL,
    price       NUMERIC(18,2) NOT NULL,
    amount      NUMERIC(18,2) NOT NULL
);

CREATE INDEX IF NOT EXISTS invoices_status_idx ON invoices(status);
