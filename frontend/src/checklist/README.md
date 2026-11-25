# Checklist de envíos

Warehouse board for packing each shipment and handing it to the carrier. Open it from the **Checklist** item in the top navigation.

## Where the data comes from

Each card is a row in the `shipments` table, which is the same table the Export page uses.

- **Save & Finish** on a PO calls `ensure_order_shipment`, which creates the order's shipment, so the PO appears on the board in **Por empacar**. Shipments created by hand on the Export page appear too.
- The **customer**, **PO**, **destination**, **carrier** (forwarder) and **tracking number** come from the shipment row, so a change on the Export page shows up here after the next refresh.
- The **item count** and **boxes** come from the order's latest packing plan. Without a plan, the item count is the total quantity on the PO.
- The packing and pickup state lives in 4 new columns on `shipments`. On Postgres they are added by migration `005_shipment_checklist.sql`, and on SQLite by `_migrate_schema`.

| Column | Values |
| --- | --- |
| `pack_status` | `to_pack` → `packed` → `picked_up` |
| `pickup_date` | `YYYY-MM-DD`, or null when no day is assigned |
| `pickup_slot` | `morning`, `afternoon`, or null |
| `picked_up_at` | UTC timestamp of the pickup |

When the migration runs, shipments dated before that day are marked `picked_up`, because they already left. Shipments dated that day or later start as `to_pack`.

## API

These endpoints are in `backend/app/checklist_routes.py` and, like every other endpoint, require sign-in.

- `GET /api/checklist` returns the shipments still in the warehouse, plus those picked up in the last 36 hours. Those are needed for the "Picked up today" counter and for undo.
- `PATCH /api/checklist/{shipment_id}` takes `{action}`, where `action` is one of `pack`, `unpack`, `assign_pickup` (with `pickup_date` and `pickup_slot`), `pick_up` or `undo_pick_up`.
  - It refuses an action that doesn't fit the current state with **409**. For example, you can't pick up a package that isn't packed.
  - The status check runs inside the `UPDATE`, so if two people act on the same package at once, only one change succeeds.

## Behaviour in the browser

- Every action updates the screen immediately and then saves to the server.
- If saving fails, the board reloads from the server and shows an error message. A 409 says another person had already changed the package.
- Changes are sent one at a time, so a pickup and its undo always reach the server in that order.
- While the page is visible, the board reloads every 30 seconds and whenever the tab becomes visible again. There is also a manual refresh button.

## Files

| Path | Role |
| --- | --- |
| `backend/app/checklist_routes.py` | API |
| `backend/app/schema/postgres/005_shipment_checklist.sql` | Postgres migration |
| `backend/tests/test_checklist.py` | API tests |
| `src/checklist/model.ts` | Types, API mapping, reducer, pure selectors |
| `src/checklist/hooks.ts` | `useToday`, debounce, `/` shortcut, viewport fill |
| `src/pages/ShipmentChecklist.tsx` | Loading, saving, refresh, undo |
| `src/components/checklist/*` | UI components |
| `src/checklist.css` | Design tokens (`--ck-*`) and styles |
| `tests/checklist.test.mjs` | Frontend logic tests |

## Run tests

```
backend/venv/Scripts/python.exe -m unittest tests.test_checklist tests.test_shipments   # from backend/
node --test frontend/tests/checklist.test.mjs
```

## Assumptions

- **Card title**: the card shows the tracking number. While a shipment has no tracking number, it shows the PO instead.
- **Destination**: the card shows the destination country name, not a city, because a shipment only stores a country code.
- **Carrier**: the carriers are the Export forwarders (DHL, DHL insured, TNT, UPS, MP, Shiplowcost, Other). A shipment with no forwarder shows "No carrier".
- **Ship date**: assigning a pickup day does not change the shipment's `ship_date` on the Export page. The two dates are independent.
- **Late beats urgent**: an unpacked or packed package with a past pickup date is *Overdue*. A packed late package can still be confirmed.
- **Drag & drop** works with a mouse only. On touch screens and with the keyboard, the buttons and the day picker do the same things.
