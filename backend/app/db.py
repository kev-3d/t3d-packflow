"""Database access layer shared by SQLite (local/dev/tests) and PostgreSQL (deployments).

The application writes plain SQL with ``?`` placeholders, the subset of syntax both engines
understand (``INSERT ... ON CONFLICT``, ``RETURNING``). This module hides the remaining
differences:

* ``connect()`` returns a :class:`Database` usable as ``with connect() as db``. On exit it
  commits (or rolls back on error) and always closes the connection / returns it to the pool.
* Rows behave like ``sqlite3.Row`` on both engines: ``row["col"]``, ``row[0]``, ``dict(row)``
  and tuple unpacking.
* ``?`` placeholders are translated to ``%s`` for psycopg; Python ``bool`` values are sent as
  ``0``/``1`` because flags are stored in integer columns.
* :data:`IntegrityError` / :data:`DatabaseError` catch the errors of either driver.
* :meth:`Database.savepoint` lets a caller tolerate a failed statement. PostgreSQL aborts the
  whole transaction after any error, so "try, catch and continue" code must use it.
"""
from __future__ import annotations

import atexit
import re
import sqlite3
import threading
from contextlib import contextmanager
from functools import lru_cache
from itertools import count
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

try:  # psycopg is only needed when a PostgreSQL URL is configured.
    import psycopg
    from psycopg_pool import ConnectionPool
except ImportError:  # pragma: no cover - exercised only on installs without psycopg
    psycopg = None  # type: ignore[assignment]
    ConnectionPool = None  # type: ignore[assignment,misc]


if psycopg is not None:
    IntegrityError: tuple[type[Exception], ...] = (sqlite3.IntegrityError, psycopg.IntegrityError)
    DatabaseError: tuple[type[Exception], ...] = (sqlite3.DatabaseError, psycopg.DatabaseError)
else:  # pragma: no cover
    IntegrityError = (sqlite3.IntegrityError,)
    DatabaseError = (sqlite3.DatabaseError,)


def is_postgres_url(url: str | None) -> bool:
    return bool(url) and url.split(":", 1)[0] in {"postgres", "postgresql"}


def normalize_postgres_url(url: str) -> str:
    # Heroku-style "postgres://" is accepted by libpq, but normalise for clarity in logs.
    return "postgresql://" + url.split("://", 1)[1] if url.startswith("postgres://") else url


# ---------------------------------------------------------------------------
# Rows


class Row(tuple):
    """Tuple of column values that can also be indexed by column name, like ``sqlite3.Row``."""

    __slots__ = ()
    _columns: dict[str, int]

    def __getitem__(self, key: Any) -> Any:  # type: ignore[override]
        if isinstance(key, str):
            return tuple.__getitem__(self, self._columns[key])
        return tuple.__getitem__(self, key)

    def keys(self) -> list[str]:
        return list(self._columns)


@lru_cache(maxsize=512)
def _row_class(columns: tuple[str, ...]) -> type[Row]:
    index: dict[str, int] = {}
    for position, name in enumerate(columns):
        index.setdefault(name, position)  # duplicate names: the first column wins, like sqlite3.Row
    return type("Row", (Row,), {"__slots__": (), "_columns": index})


def _pg_row_factory(cursor: Any) -> Any:
    if cursor.description is None:
        return None
    row_class = _row_class(tuple(column.name for column in cursor.description))
    return lambda values: tuple.__new__(row_class, values)


# ---------------------------------------------------------------------------
# SQL translation

_TOKEN = re.compile(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|--[^\n]*|\?|%")


@lru_cache(maxsize=2048)
def to_pyformat(sql: str) -> str:
    """Translate qmark placeholders to psycopg's ``%s``; escape literal ``%`` as ``%%``."""

    def replace(match: re.Match[str]) -> str:
        token = match.group(0)
        if token == "?":
            return "%s"
        if token == "%":
            return "%%"
        return token.replace("%", "%%")  # psycopg also parses '%' inside literals

    return _TOKEN.sub(replace, sql)


def _adapt(params: Sequence[Any] | None) -> tuple[Any, ...]:
    if not params:
        return ()
    return tuple(int(value) if isinstance(value, bool) else value for value in params)


# ---------------------------------------------------------------------------
# Connection wrapper


class Database:
    """A connection plus the unit of work opened by ``with connect() as db``."""

    _savepoint_ids = count(1)

    def __init__(self, raw: Any, dialect: str, release: Any) -> None:
        self.raw = raw
        self.dialect = dialect
        self._release = release
        self._closed = False

    # -- statements -------------------------------------------------------
    def execute(self, sql: str, params: Sequence[Any] | None = None) -> Any:
        if self.dialect == "sqlite":
            return self.raw.execute(sql, _adapt(params))
        values = _adapt(params)
        return self.raw.execute(to_pyformat(sql) if values else sql, values or None)

    def executemany(self, sql: str, rows: Iterable[Sequence[Any]]) -> Any:
        if self.dialect == "sqlite":
            return self.raw.executemany(sql, [_adapt(row) for row in rows])
        cursor = self.raw.cursor()
        cursor.executemany(to_pyformat(sql), [_adapt(row) for row in rows])
        return cursor

    def executescript(self, script: str) -> None:
        if self.dialect == "sqlite":
            self.raw.executescript(script)
        else:
            self.raw.execute(script)  # simple protocol: several statements, no parameters

    def insert_returning_id(self, sql: str, params: Sequence[Any] | None = None) -> int:
        """Run an ``INSERT ... RETURNING id`` statement and return the new id."""
        return int(self.execute(sql, params).fetchone()[0])

    # -- transactions -----------------------------------------------------
    @contextmanager
    def savepoint(self) -> Iterator[None]:
        """Undo only the statements in this block when it raises; the exception propagates."""
        name = f"sp_{next(self._savepoint_ids)}"
        self.execute(f"SAVEPOINT {name}")
        try:
            yield
        except BaseException:
            self.execute(f"ROLLBACK TO SAVEPOINT {name}")
            self.execute(f"RELEASE SAVEPOINT {name}")
            raise
        self.execute(f"RELEASE SAVEPOINT {name}")

    def commit(self) -> None:
        self.raw.commit()

    def rollback(self) -> None:
        self.raw.rollback()

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._release(self.raw)

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        try:
            if exc_type is None:
                self.commit()
            else:
                self.rollback()
        finally:
            self.close()

    def __del__(self) -> None:  # safety net for connections opened without ``with``
        try:
            if not self._closed:
                self.rollback()
                self.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Engines


def connect_sqlite(path: Path) -> Database:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = sqlite3.connect(path)
    raw.row_factory = sqlite3.Row
    raw.execute("PRAGMA foreign_keys = ON")
    return Database(raw, "sqlite", lambda connection: connection.close())


_pools: dict[str, Any] = {}
_pools_lock = threading.Lock()
_created_schemas: set[tuple[str, str]] = set()
_SCHEMA_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def get_pool(url: str, max_size: int = 5) -> Any:
    if psycopg is None or ConnectionPool is None:
        raise RuntimeError("DATABASE_URL points to PostgreSQL but psycopg is not installed.")
    with _pools_lock:
        pool = _pools.get(url)
        if pool is None:
            pool = ConnectionPool(
                normalize_postgres_url(url),
                min_size=1,
                max_size=max_size,
                max_idle=300,
                open=True,
                # prepare_threshold=None keeps compatibility with transaction-mode poolers
                # (PgBouncer, Neon/Supabase "-pooler" endpoints).
                kwargs={"row_factory": _pg_row_factory, "prepare_threshold": None, "autocommit": False},
            )
            _pools[url] = pool
        return pool


def connect_postgres(url: str, schema: str | None = None, max_size: int = 5) -> Database:
    """Check a connection out of the pool for ``url``.

    ``schema`` (used by the test-suite) isolates the work in its own schema, created on first use.
    """
    pool = get_pool(url, max_size)
    raw = pool.getconn()
    try:
        if schema:
            if not _SCHEMA_NAME.match(schema):
                raise ValueError(f"Invalid schema name {schema!r}")
            if (url, schema) not in _created_schemas:
                raw.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
                raw.commit()
                _created_schemas.add((url, schema))
            raw.execute(f'SET search_path TO "{schema}"')
        else:
            raw.execute("RESET search_path")
    except BaseException:
        pool.putconn(raw)
        raise
    return Database(raw, "postgres", pool.putconn)


def drop_created_schemas() -> None:
    """Drop the schemas created through ``connect_postgres(schema=...)`` (test clean-up)."""
    for url, schema in list(_created_schemas):
        try:
            with psycopg.connect(normalize_postgres_url(url), autocommit=True) as raw:  # type: ignore[union-attr]
                raw.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        except Exception:
            pass
        _created_schemas.discard((url, schema))


def close_pools() -> None:
    with _pools_lock:
        for pool in _pools.values():
            pool.close()
        _pools.clear()


atexit.register(close_pools)
