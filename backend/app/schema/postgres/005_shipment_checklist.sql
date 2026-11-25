-- Shipment checklist (warehouse): packing and carrier pickup state of each shipment.
-- pack_status: to_pack -> packed -> picked_up. pickup_slot: morning | afternoon (NULL = no slot yet).
ALTER TABLE shipments ADD COLUMN IF NOT EXISTS pack_status TEXT NOT NULL DEFAULT 'to_pack';
ALTER TABLE shipments ADD COLUMN IF NOT EXISTS pickup_date TEXT;
ALTER TABLE shipments ADD COLUMN IF NOT EXISTS pickup_slot TEXT;
ALTER TABLE shipments ADD COLUMN IF NOT EXISTS picked_up_at TEXT;

ALTER TABLE shipments ADD CONSTRAINT shipments_pack_status_check
    CHECK (pack_status IN ('to_pack', 'packed', 'picked_up'));
ALTER TABLE shipments ADD CONSTRAINT shipments_pickup_slot_check
    CHECK (pickup_slot IS NULL OR pickup_slot IN ('morning', 'afternoon'));

-- Shipments dated before this feature existed already left the warehouse: keep them off the board.
UPDATE shipments SET pack_status = 'picked_up', picked_up_at = ship_date
 WHERE ship_date < to_char(current_date, 'YYYY-MM-DD');

CREATE INDEX IF NOT EXISTS shipments_pack_status_idx ON shipments (pack_status);
