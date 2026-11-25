# Análisis y Plan de Deploy — PackFlow (SQLite → PostgreSQL)

> Fecha: 2026-11-23 · Estado: propuesta aprobada para implementación
> Objetivo: desplegar la aplicación en un entorno de pruebas gratuito, fiable y de buena calidad, migrando la base de datos de SQLite a PostgreSQL (también como base para producción).

---

## 1. Análisis del proyecto (hallazgos relevantes para el deploy)

| Aspecto | Estado actual | Implicación para deploy |
|---|---|---|
| **Arquitectura** | Monorepo: FastAPI (Python 3.12) + React 19/Vite. Un solo contenedor sirve API y frontend compilado (`Dockerfile` multi-stage, target `runtime`) | ✅ Ideal para PaaS gratuitos: 1 solo servicio, 1 URL, sin CORS |
| **Deploy previo** | Ya existe `render.yaml` (Blueprint Render Free) y `docs/DEPLOY.md` | ✅ Punto de partida listo; solo falta la BD externa |
| **Base de datos** | `sqlite3` **crudo, sin ORM**: ~145 llamadas SQL en 3 archivos (`main.py` 99, `database.py` 31, `requirements_routes.py` 15) | ⚠️ La migración a PG es el trabajo principal: dialecto SQLite en todas partes |
| **Dialecto SQLite a convertir** | Placeholders `?`, `AUTOINCREMENT`, `INSERT OR IGNORE/REPLACE`, `PRAGMA table_info`, `cursor.lastrowid`, `sqlite3.Row`, `sqlite3.IntegrityError`, `executescript` | Requiere capa adaptadora (ver Fase 1) |
| **Archivos binarios** | PDFs fuente y reportes guardados como `BLOB` **dentro de la BD** (`source_documents`, `analysis_reports`) | ✅ Bien para el test: al pasar a PG (`BYTEA`) la persistencia queda resuelta sin object storage |
| **Transacciones** | `with connect() as db` — el context manager de sqlite3 hace commit/rollback pero **no cierra** la conexión (fuga actual) | El adaptador debe hacer commit + close correctamente |
| **Salud** | `GET /api/health` → `{"database":"sqlite"}` (hardcodeado) | Debe reflejar el motor real |
| **Cómputo** | Motor de packing 3D CPU-bound (búsqueda acotada) en el servidor | ⚠️ En free tiers con 0.1 vCPU los cálculos serán lentos; 1 worker uvicorn |
| **Seguridad** | Sin autenticación ni permisos (documentado en README) | ⚠️ Solo datos de prueba no confidenciales; token/basic auth opcional |
| **Tests** | 17 archivos `unittest` + build de frontend | ✅ Red de seguridad para validar la migración |
| **Config** | `.env` loader propio, vars `PACKFLOW_*`, Slack opcional | Añadir `DATABASE_URL` como variable estándar |

**Conclusión clave:** el mayor riesgo del deploy gratuito (Render Free tiene disco efímero → se pierden órdenes y PDFs en cada reinicio) **queda resuelto exactamente por la migración a PostgreSQL externo**. La app ya es "stateless-friendly".

---

## 2. Opciones gratuitas (verificado a nov-2026)

### 2a. Hosting de la aplicación

| Opción | Pros | Contras | Veredicto |
|---|---|---|---|
| **Render Free** (Docker) | `render.yaml` ya existe en el repo; deploy en minutos sin tarjeta; HTTPS y dominio incluidos; health check nativo; 750 h/mes alcanzan para 1 servicio | 512 MB RAM y CPU fraccionada (packing lento); duerme tras 15 min idle (~1 min de arranque en frío); disco efímero (irrelevante con PG externo); sin backups ni shell | ⭐ **Recomendada para test** |
| **Fly.io (plan Free, $5/mes de crédito)** | Crédito mensual real: una VM `shared-cpu-1x` 256 MB (~$2.19/mes) corre **24/7 sin dormir** dentro del crédito; más CPU que Render Free; Docker nativo; regiones globales | **Exige tarjeta**; al superar $5 pasas a pago automático; CLI y configuración más compleja; el Postgres gestionado ($4/mes) consume casi todo el crédito → usar Neon aparte | ⭐ Alternativa si se quiere demo siempre despierta |
| **Google Cloud Run** (free tier perpetuo) | 2M requests + 360k vCPU-seg/mes gratis siempre; escala a cero; CPU de hasta 1-2 vCPU por request (mejor para el packing); muy fiable | Exige tarjeta y cuenta GCP; setup más largo (Artifact Registry, IAM, billing); cold starts; más piezas que mantener | Buena si ya usas GCP |
| **Oracle Cloud Always Free** (VM ARM) | 4 OCPU ARM + 24 GB RAM **gratis para siempre**; sin cold starts; corres `docker compose` con Postgres incluido; entorno idéntico a producción | Registro con rechazos frecuentes de tarjeta y falta de capacidad ARM; tú gestionas SO, parches, TLS, backups y firewall; mayor carga operativa | Mejor "producción gratis" si aceptas operarla |
| **Koyeb** | Históricamente buen free tier | **Cerrado a cuentas nuevas desde feb-2026** (Pro desde $29/mes) | ❌ Descartada |
| **Railway / Heroku** | DX excelente | Sin tier gratuito real (solo crédito de prueba) | ❌ Descartadas |
| **Hugging Face Spaces (Docker)** | 2 vCPU / 16 GB gratis en apariencia | Docs recientes indican que Spaces Docker en `cpu-basic` ahora requieren suscripción PRO; duerme tras ~48 h; orientación ML, no apps de negocio | ❌ No fiable para este caso |
| **Vercel / Cloudflare Pages + backend aparte** | Excelente para el frontend | Partir la app en 2 despliegues añade CORS, variable de API URL y complejidad; el backend Python no encaja bien en serverless JS | ❌ Innecesario: la imagen única ya sirve todo |

### 2b. PostgreSQL gestionado gratuito

| Opción | Pros | Contras | Veredicto |
|---|---|---|---|
| **Neon Free** | **Sin expiración**; 1 GB/proyecto (hasta 20 GB total), 100 proyectos; 100 CU-h/mes por proyecto; scale-to-zero con despertar en ~0.5-1 s; branching de BD (ideal para probar migraciones); sin tarjeta; pooling integrado (PgBouncer) | Cold start sub-segundo tras inactividad; 100 CU-h/mes se agotan si dejas conexiones abiertas 24/7 (usar pooled + scale-to-zero); sin PITR largo en free | ⭐ **Recomendada** |
| **Supabase Free** | 500 MB; plataforma completa (auth, storage — útil a futuro); fiable y popular | **Pausa el proyecto tras ~7 días de inactividad** (restauración manual); solo 2 proyectos activos; más peso del que necesitas | ✅ Alternativa sólida |
| **Render Postgres Free** | Integración nativa con el servicio (misma red, `fromDatabase`) | **Expira a los 30 días** + 14 de gracia y luego **borran los datos**; 1 GB; sin backups; sin pooling | ❌ Trampa para este requisito |
| **Fly Managed Postgres (Developer)** | $4/mes pagados del crédito Free; backups diarios; failover | Consume casi todo el crédito de $5; 256 MB RAM; pausa tras 15 min | ⚠️ Solo si eliges Fly y aceptas pagar el exceso |
| **Postgres autogestionado (Oracle VM / VPS)** | Control total, sin límites artificiales | Backups, parches, TLS y monitoreo son tu responsabilidad | Para la fase producción-Oracle |

### Arquitectura recomendada

```
Tester ──HTTPS──► Render Free (Docker: FastAPI + React, 1 servicio)
                        │  DATABASE_URL (pooled, TLS)
                        ▼
                  Neon Free Postgres (región Frankfurt = misma que Render)
```

**Coste: $0, sin tarjeta, datos persistentes.** Camino de producción natural: Render de pago ($7/mes, sin sleep) + Neon de pago, o Fly.io PAYG, u Oracle Always Free si el presupuesto debe seguir siendo $0.

---

## 3. Plan detallado de deploy

### Fase 0 — Preparación (≈0.5 día)

1. Subir el repo a GitHub (rama `feat/postgres-deploy`); verificar que `.env`, `backend/data/` y `venv/` están excluidos de la imagen (`.dockerignore` ya existe — auditarlo).
2. Crear cuentas: GitHub, Render, Neon (ninguna pide tarjeta).
3. Línea base local: `docker compose up -d --build` + suite de tests (`python -m unittest discover -s tests`) + `npm run build`. Todo debe pasar **antes** de tocar nada.

### Fase 1 — Migración SQLite → PostgreSQL (≈2–3 días, el núcleo del trabajo)

Estrategia recomendada: **capa adaptadora mínima con doble motor** (no reescribir a ORM ahora). Mantiene SQLite para desarrollo/tests rápidos y habilita PG con `DATABASE_URL`.

1. **Dependencias:** añadir `psycopg[binary]` y `psycopg_pool` a `backend/requirements.txt`.
2. **Nuevo módulo `backend/app/db.py`** que reemplace el `connect()` de `database.py`:
   - Si `DATABASE_URL` empieza con `postgres://` → conexión psycopg (pool pequeño, `dict_row`); si no → SQLite actual (fallback local).
   - Wrapper que conserva la interfaz usada en los 3 archivos: `.execute(sql, params)` traduciendo `?` → `%s`, filas como dict (compatible con `sqlite3.Row`), `.executemany`, y cierre + commit correctos al salir del `with` (corrige la fuga actual).
   - `cursor.lastrowid` (6 usos) → `INSERT ... RETURNING id`.
   - Excepción común `IntegrityError` para reemplazar los ~7 `except sqlite3.IntegrityError` de `main.py` y `requirements_routes.py`.
3. **DDL PostgreSQL** en `init_db()` (rama PG):
   - `INTEGER PRIMARY KEY AUTOINCREMENT` → `BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY`
   - `REAL` → `DOUBLE PRECISION`; `BLOB` → `BYTEA`; fechas ISO se mantienen como `TEXT` en esta fase (cambio mínimo; migrar a `TIMESTAMPTZ` después).
   - El índice funcional `lower(trim(customer))` es válido en PG tal cual.
   - `PRAGMA table_info` (migraciones aditivas) → `information_schema.columns`; `executescript` → sentencias separadas.
4. **Dialecto DML:**
   - `INSERT OR IGNORE` → `ON CONFLICT ... DO NOTHING` (las restricciones únicas ya existen: `app_settings.key`, `analysis_reports.plan_id`).
   - `INSERT OR REPLACE` → `ON CONFLICT ... DO UPDATE` (`app_settings`, `source_documents`, `shipping_quotes`).
   - Revisar `LIKE` (SQLite es case-insensitive en ASCII, PG no) → `ILIKE` donde aplique; revisar `SELECT` sin `ORDER BY` cuyo orden se asuma estable.
5. **Salud:** `/api/health` devuelve el motor real (`postgres`/`sqlite`).
6. **Script de migración de datos** `backend/scripts/migrate_sqlite_to_pg.py`: lee el SQLite existente, inserta preservando IDs, reajusta secuencias (`setval`), verifica conteos por tabla. Incluye los BLOB (PDFs).
7. **Tests y CI:** la suite actual sigue corriendo en SQLite; añadir job de GitHub Actions con servicio `postgres:16` que ejecuta la misma suite con `DATABASE_URL` → garantía de paridad entre motores.

### Fase 2 — Validación local (≈0.5–1 día)

1. Añadir servicio `postgres:16-alpine` a `docker-compose.yml` con `DATABASE_URL` para el app (el volumen SQLite queda solo para el modo dev).
2. Ejecutar suite completa contra **ambos** motores.
3. Smoke manual end-to-end: orden desde PDF / email / manual → validar y calcular → exportar Excel y PDF → guardar reporte → `DELETE /api/orders` → reiniciar contenedor y confirmar que catálogo y reportes **persisten**.
4. Migrar una copia de `backend/data/packflow.db` real con el script y comparar conteos.
5. Medir tiempo de cálculo de packing (baseline para comparar con el free tier).

### Fase 3 — Aprovisionar Neon (≈0.5 día)

1. Crear proyecto en **AWS eu-central-1 (Frankfurt)** — misma región que `render.yaml` (`region: frankfurt`) para minimizar latencia por consulta.
2. Copiar la **connection string pooled** (con `-pooler`, TLS `require`).
3. Ejecutar el script de migración (o dejar que `init_db()` cree el esquema) apuntando a Neon.
4. Verificar con `psql`/cliente: tablas, catálogo de referencia, secuencias.

### Fase 4 — Deploy en Render (≈0.5 día)

1. Actualizar `render.yaml`: añadir `DATABASE_URL` (`sync: false`, se pega en el dashboard como secreto), mantener `healthCheckPath: /api/health` y `PACKFLOW_SEED_REFERENCE_CATALOG=true`.
2. Push a GitHub → Render Blueprint detecta el cambio → deploy automático.
3. Verificar `https://…onrender.com/api/health` → `{"status":"ok","database":"postgres"}`.
4. Smoke completo por la URL pública (mismos pasos de Fase 2).
5. **Prueba de persistencia clave:** crear una orden, forzar un redeploy y confirmar que sigue ahí (esto es lo que el deploy SQLite-free no podía hacer).

### Fase 5 — Plan de pruebas para testers (≈0.5 día)

Checklist a entregar con la URL:

- Flujos: PDF (Rev1/Hava/generic), email, manual; revisión de ítems; cálculo con los 3 objetivos; recomendaciones; export Excel/PDF; cotizaciones DHL; borrado total.
- Concurrencia: 1 worker — probar 2-3 usuarios simultáneos y medir.
- Rendimiento: anotar tiempos de cálculo (CPU fraccionada) y el cold start (~1 min Render + ~1 s Neon).
- Reglas: solo datos de ejemplo/no confidenciales (no hay login); la URL da acceso total a quien la tenga. Opcional: middleware de token compartido antes de compartir la URL.

### Fase 6 — Camino a producción (cuando el test se apruebe)

| Paso | Acción |
|---|---|
| Cómputo | Render Starter $7/mes (sin sleep, más RAM) o Fly PAYG; alternativa $0: VM Oracle Always Free con compose |
| BD | Neon Launch ($19/mes, PITR) o Supabase Pro ($25/mes, backups diarios); mientras tanto, backup lógico programado (`pg_dump` semanal vía GitHub Actions) |
| Seguridad | Autenticación (hoy inexistente), TLS ya provisto por la plataforma, secretos solo en el dashboard |
| Archivos | Mover PDFs de `BYTEA` a object storage (Cloudflare R2 free 10 GB / S3) cuando la BD crezca |
| Esquema | Migraciones formales (Alembic o SQL versionado) y fechas a `TIMESTAMPTZ` |
| Operación | Monitoreo de uptime externo, alertas Slack (ya hay integración), CI/CD con tests en PG antes de cada deploy |

**Estimación total: ~4–6 días de trabajo**, con la Fase 1 concentrando el riesgo técnico.

---

## 4. Riesgos principales y mitigación

| Riesgo | Mitigación |
|---|---|
| Diferencias de dialecto no detectadas (LIKE, orden implícito, booleanos) | Suite de tests corriendo contra ambos motores en CI |
| 100 CU-h/mes de Neon agotadas por conexiones ociosas | Usar endpoint pooled + scale-to-zero; pool pequeño con cierre idle |
| Packing lento en 0.1 vCPU de Render Free | Medir en Fase 2/5; si es inaceptable, Fly.io (más CPU dentro del crédito) |
| 750 h/mes de Render (1 servicio 24/7 ≈ 744 h) | Un solo servicio; el sleep por inactividad deja margen |
| Sin auth en URL pública | Datos no confidenciales + token opcional antes de compartir |
| Free Postgres de Render "parece" la opción natural | **Evitarla: expira a 30 días y borra datos** |

---

## 5. Referencias

- Render Free (límites, sleep, 750 h, Postgres 30 días): https://render.com/docs/free
- Neon Free plan: https://neon.com/docs/introduction/plans
- Supabase pausado por inactividad: https://supabase.com/docs/guides/platform/free-project-pausing
- Fly.io pricing (plan Free $5/mes): https://fly.io/pricing/
- Guía de deploy actual del repo: `docs/DEPLOY.md` · Guía Docker: `docs/DOCKER.md`