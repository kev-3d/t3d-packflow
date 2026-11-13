# Docker

Requisitos: Docker Desktop (o Docker Engine) con Compose v2.

| Archivo | Uso |
| --- | --- |
| `Dockerfile` | Build multi-stage. Target por defecto `runtime` (producción, usado también por Render). Targets `backend-dev` y `frontend-dev` para desarrollo. |
| `docker-compose.yml` | Stack de producción: un contenedor sirve React + API en el puerto 10000 con datos persistentes. |
| `compose.dev.yml` | Stack de desarrollo con hot reload: API (8001) + Vite (5173). |
| `.env.example` | Puertos y variables configurables. Copiar a `.env`. |

## Producción local

```bash
docker compose up -d --build
```

- App: http://localhost:10000 — Health: http://localhost:10000/api/health
- La base SQLite vive en el volumen `packflow_packflow-data` (`/app/data`), así que sobrevive a reinicios y rebuilds.
- El contenedor corre como usuario sin privilegios (uid 10001), con sistema de archivos de solo lectura salvo `/app/data` y `/tmp`, y con `HEALTHCHECK`.

Comandos útiles:

```bash
docker compose ps
docker compose logs -f app
docker compose down
```

`docker compose down -v` **borra el volumen con la base de datos**.

Backup / restauración de la base:

```bash
docker compose cp app:/app/data/packflow.db ./packflow-backup.db
docker compose cp ./packflow-backup.db app:/app/data/packflow.db && docker compose restart app
```

## Desarrollo con hot reload

```bash
docker compose -f compose.dev.yml up --build
```

- Frontend (Vite): http://localhost:5173 — hace proxy de `/api` hacia el servicio `api`.
- API: http://localhost:8001/api/health
- `backend/app` y `frontend` se montan desde el host; los cambios se recargan solos (polling activado para funcionar en Windows/macOS).
- `node_modules` vive dentro del contenedor. Si cambias `package.json` o `requirements.txt`, reconstruye: `docker compose -f compose.dev.yml up --build`.
- La base de desarrollo usa un volumen separado (`packflow-dev_packflow-dev-data`) y por defecto no carga el catálogo de referencia (`PACKFLOW_SEED_REFERENCE_CATALOG=false`).

## Variables de entorno

| Variable | Default | Descripción |
| --- | --- | --- |
| `PACKFLOW_PORT` | `10000` | Puerto del host para el stack de producción. |
| `PACKFLOW_API_PORT` / `PACKFLOW_WEB_PORT` | `8001` / `5173` | Puertos del host en desarrollo. |
| `PACKFLOW_SEED_REFERENCE_CATALOG` | `true` prod / `false` dev | Carga productos y cajas de referencia al iniciar. |
| `PACKFLOW_DB_PATH` | `/app/data/packflow.db` | Ruta de la base SQLite dentro del contenedor. |

## Render

`render.yaml` sigue usando el mismo `Dockerfile`; Render construye el último stage (`runtime`), así que el despliegue no cambia. Ver [DEPLOY.md](DEPLOY.md).
