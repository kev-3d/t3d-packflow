-- TSCA certification for US shipments: declaration fields on products and one
-- editable draft per order.
ALTER TABLE products ADD COLUMN IF NOT EXISTS ean TEXT NOT NULL DEFAULT '';
ALTER TABLE products ADD COLUMN IF NOT EXISTS tsca_name TEXT NOT NULL DEFAULT '';
ALTER TABLE products ADD COLUMN IF NOT EXISTS tsca_description TEXT NOT NULL DEFAULT '';

CREATE TABLE IF NOT EXISTS tsca_declarations (
    order_id BIGINT PRIMARY KEY REFERENCES orders(id) ON DELETE CASCADE,
    draft_json TEXT NOT NULL,
    confirmed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
