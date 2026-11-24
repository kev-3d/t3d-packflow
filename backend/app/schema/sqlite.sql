-- PackFlow schema for SQLite (local development and tests).
CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sku TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    length_cm REAL NOT NULL CHECK(length_cm > 0),
    width_cm REAL NOT NULL CHECK(width_cm > 0),
    height_cm REAL NOT NULL CHECK(height_cm > 0),
    weight_kg REAL NOT NULL CHECK(weight_kg > 0),
    rotation_allowed INTEGER NOT NULL DEFAULT 1,
    fragile INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS box_types (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    length_cm REAL NOT NULL CHECK(length_cm > 0),
    width_cm REAL NOT NULL CHECK(width_cm > 0),
    height_cm REAL NOT NULL CHECK(height_cm > 0),
    empty_weight_kg REAL NOT NULL CHECK(empty_weight_kg >= 0),
    max_weight_kg REAL NOT NULL CHECK(max_weight_kg > 0),
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_number TEXT NOT NULL UNIQUE,
    client TEXT NOT NULL,
    vendor TEXT NOT NULL DEFAULT '',
    order_date TEXT,
    requested_date TEXT,
    currency TEXT NOT NULL DEFAULT 'USD',
    source_filename TEXT,
    source_type TEXT NOT NULL DEFAULT 'pdf',
    delivery_address TEXT NOT NULL DEFAULT '',
    billing_address TEXT NOT NULL DEFAULT '',
    terms TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    subtotal REAL NOT NULL DEFAULT 0,
    tax_amount REAL NOT NULL DEFAULT 0,
    total_amount REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'needs_review',
    confidence REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS order_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    product_id INTEGER REFERENCES products(id),
    sku_raw TEXT NOT NULL,
    description TEXT NOT NULL,
    quantity INTEGER NOT NULL CHECK(quantity > 0),
    unit_price REAL NOT NULL DEFAULT 0,
    line_number INTEGER,
    requested_date TEXT,
    tax_label TEXT NOT NULL DEFAULT '',
    discount_percent REAL NOT NULL DEFAULT 0,
    amount REAL NOT NULL DEFAULT 0,
    is_suggested INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS packing_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    box_count INTEGER NOT NULL,
    average_utilization REAL NOT NULL,
    total_weight_kg REAL NOT NULL,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_documents (
    order_id INTEGER PRIMARY KEY REFERENCES orders(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    content BLOB NOT NULL
);

CREATE TABLE IF NOT EXISTS analysis_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    plan_id INTEGER UNIQUE REFERENCES packing_plans(id) ON DELETE SET NULL,
    filename TEXT NOT NULL,
    pdf_content BLOB NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS clients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer TEXT NOT NULL,
    phone_number TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    contact_name TEXT NOT NULL DEFAULT '',
    address TEXT NOT NULL DEFAULT '',
    country TEXT NOT NULL DEFAULT '',
    shipping_address TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS shipping_quotes (
    order_id INTEGER PRIMARY KEY REFERENCES orders(id) ON DELETE CASCADE,
    plan_id INTEGER,
    inputs_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    tariff_version TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_clients_customer ON clients(lower(trim(customer)));
CREATE TABLE IF NOT EXISTS client_requirements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company TEXT NOT NULL,
    company_key TEXT NOT NULL UNIQUE,
    aliases TEXT NOT NULL DEFAULT '',
    client_id INTEGER REFERENCES clients(id) ON DELETE SET NULL,
    invoice_email TEXT NOT NULL DEFAULT '',
    payment TEXT NOT NULL DEFAULT '',
    pricing_requirements TEXT NOT NULL DEFAULT '',
    docs_required TEXT NOT NULL DEFAULT '',
    courier TEXT NOT NULL DEFAULT '',
    comments TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_order_items_order ON order_items(order_id);
CREATE INDEX IF NOT EXISTS idx_plans_order ON packing_plans(order_id, id DESC);

-- TSCA certification draft for a US shipment: one editable draft per order.
CREATE TABLE IF NOT EXISTS tsca_declarations (
    order_id INTEGER PRIMARY KEY REFERENCES orders(id) ON DELETE CASCADE,
    draft_json TEXT NOT NULL,
    confirmed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Application users. Identities live in Clerk; this table only holds who may use the app
-- and with which role. clerk_user_id is linked on the first sign-in (matched by verified email).
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    clerk_user_id TEXT UNIQUE,
    email TEXT NOT NULL UNIQUE,
    full_name TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('admin', 'user')),
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_login_at TEXT
);
