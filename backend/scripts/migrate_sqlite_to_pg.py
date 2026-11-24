"""Copy a PackFlow SQLite database into PostgreSQL.

Usage (from backend/):
    python -m scripts.migrate_sqlite_to_pg --sqlite data/packflow.db --database-url postgresql://...
    python -m scripts.migrate_sqlite_to_pg ... --dry-run   # everything, then roll back
    python -m scripts.migrate_sqlite_to_pg ... --force     # empty a non-empty target first

Steps:
1. Works on a temporary copy of the SQLite file, upgraded to the current schema
   (the source file is never modified).
2. Pre-flight checks for data SQLite accepted but PostgreSQL rejects (orphan foreign
   keys, duplicate client names, text in numeric columns, invalid JSON).
3. Creates the PostgreSQL schema (schema/postgres/*.sql) and copies every table in
   dependency order, preserving ids, then resets the identity sequences.
4. Verifies row counts and checksums, all inside one transaction: on any error
   nothing is written.
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import _apply_postgres_migrations, _apply_reference_packing_data, _migrate_schema, SCHEMA_DIR  # noqa: E402
from app.db import Database, connect_postgres, connect_sqlite, is_postgres_url  # noqa: E402

# Parents before children so foreign keys hold on every insert.
TABLES = [
    "app_settings",
    "products",
    "box_types",
    "clients",
    "client_requirements",
    "orders",
    "order_items",
    "packing_plans",
    "source_documents",
    "shipping_quotes",
    "analysis_reports",
]
JSON_COLUMNS = {
    "packing_plans": ["result_json"],
    "shipping_quotes": ["inputs_json", "result_json"],
    "box_types": ["capacities"],
}
BATCH = 500


class MigrationError(RuntimeError):
    pass


def upgraded_copy(source: Path, folder: Path) -> Database:
    """Copy the SQLite file and bring the copy to the current schema."""
    copy = folder / "source.db"
    # sqlite3's own context manager does not close connections; closing() does.
    with closing(sqlite3.connect(f"file:{source}?mode=ro", uri=True)) as raw, closing(sqlite3.connect(copy)) as target:
        raw.backup(target)  # consistent snapshot, even if the app is using the file
    db = connect_sqlite(copy)
    db.executescript((SCHEMA_DIR / "sqlite.sql").read_text(encoding="utf-8"))
    _migrate_schema(db)
    _apply_reference_packing_data(db)
    db.commit()
    return db


def pg_columns(db: Database, table: str) -> dict[str, str]:
    rows = db.execute(
        """SELECT column_name, data_type FROM information_schema.columns
           WHERE table_schema = current_schema() AND table_name = ? ORDER BY ordinal_position""",
        (table,),
    ).fetchall()
    return {row["column_name"]: row["data_type"] for row in rows}


def sqlite_columns(db: Database, table: str) -> list[str]:
    return [row["name"] for row in db.execute(f"PRAGMA table_info({table})")]


def preflight(source: Database) -> list[str]:
    problems: list[str] = []
    for row in source.execute("PRAGMA foreign_key_check"):
        problems.append(f"Orphan row: {row[0]} rowid={row[1]} references missing {row[2]}")
    for row in source.execute(
        "SELECT lower(trim(customer)) AS k, COUNT(*) AS n FROM clients GROUP BY k HAVING n > 1"
    ):
        problems.append(f"Duplicate client name: {row['k']!r} ({row['n']} rows)")
    for table, columns in JSON_COLUMNS.items():
        for column in columns:
            for row in source.execute(f"SELECT rowid, {column} FROM {table}"):
                try:
                    json.loads(row[1])
                except (TypeError, ValueError):
                    problems.append(f"Invalid JSON in {table}.{column} rowid={row[0]}")
    return problems


def convert(value: Any, pg_type: str, where: str) -> Any:
    if value is None:
        return None
    if pg_type == "bytea":
        return value.encode("utf-8") if isinstance(value, str) else bytes(value)
    if pg_type in {"double precision", "real", "numeric"}:
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise MigrationError(f"{where}: {value!r} is not a number") from exc
        if math.isnan(number):
            raise MigrationError(f"{where}: NaN is not allowed")
        return number
    if pg_type in {"integer", "bigint", "smallint"}:
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise MigrationError(f"{where}: {value!r} is not an integer") from exc
        if number != int(number):
            raise MigrationError(f"{where}: {value!r} is not an integer")
        return int(number)
    if pg_type == "text" and not isinstance(value, str):
        return value.decode("utf-8") if isinstance(value, bytes) else str(value)
    return value


def target_is_empty(target: Database) -> bool:
    return all(target.execute(f"SELECT NOT EXISTS (SELECT 1 FROM {table})").fetchone()[0] for table in TABLES)


def copy_table(source: Database, target: Database, table: str) -> int:
    target_types = pg_columns(target, table)
    columns = [column for column in sqlite_columns(source, table) if column in target_types]
    missing = sorted(set(target_types) - set(columns))
    if missing:
        print(f"  {table}: columns only in PostgreSQL use their defaults: {', '.join(missing)}")
    order = "rowid"
    insert = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})"
    cursor = source.execute(f"SELECT {', '.join(columns)} FROM {table} ORDER BY {order}")
    copied = 0
    while batch := cursor.fetchmany(BATCH):
        rows = [
            tuple(convert(row[i], target_types[column], f"{table}.{column}") for i, column in enumerate(columns))
            for row in batch
        ]
        target.executemany(insert, rows)
        copied += len(rows)
    return copied


def reset_sequences(target: Database) -> None:
    for table in TABLES:
        if "id" not in pg_columns(target, table) or table == "source_documents":
            continue
        target.execute(
            f"""SELECT setval(pg_get_serial_sequence('{table}', 'id'),
                              COALESCE((SELECT MAX(id) FROM {table}), 1),
                              (SELECT MAX(id) FROM {table}) IS NOT NULL)"""
        )


CHECKSUMS = {
    "orders": "SELECT COUNT(*), COALESCE(SUM(id), 0), ROUND(COALESCE(SUM(total_amount), 0) * 100) FROM orders",
    "order_items": "SELECT COUNT(*), COALESCE(SUM(id), 0), COALESCE(SUM(quantity), 0) FROM order_items",
    "packing_plans": "SELECT COUNT(*), COALESCE(SUM(id), 0), COALESCE(SUM(box_count), 0) FROM packing_plans",
}
BLOB_SIZES = {
    "source_documents": "content",
    "analysis_reports": "pdf_content",
}


def verify(source: Database, target: Database) -> list[str]:
    problems: list[str] = []
    for table in TABLES:
        expected = source.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        actual = target.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        status = "ok" if expected == actual else "MISMATCH"
        print(f"  {table:<22} sqlite={expected:<7} postgres={actual:<7} {status}")
        if expected != actual:
            problems.append(f"{table}: {expected} rows in SQLite, {actual} in PostgreSQL")
    for table, sql in CHECKSUMS.items():
        expected = tuple(float(v) for v in source.execute(sql).fetchone())
        actual = tuple(float(v) for v in target.execute(sql).fetchone())
        if expected != actual:
            problems.append(f"{table} checksum differs: {expected} vs {actual}")
    for table, column in BLOB_SIZES.items():
        expected = source.execute(f"SELECT COALESCE(SUM(length({column})), 0) FROM {table}").fetchone()[0]
        actual = target.execute(f"SELECT COALESCE(SUM(octet_length({column})), 0) FROM {table}").fetchone()[0]
        if int(expected) != int(actual):
            problems.append(f"{table}.{column}: {expected} bytes in SQLite, {actual} in PostgreSQL")
    return problems


def migrate(sqlite_path: Path, database_url: str, *, dry_run: bool = False, force: bool = False) -> None:
    if not sqlite_path.is_file():
        raise MigrationError(f"SQLite file not found: {sqlite_path}")
    if not is_postgres_url(database_url):
        raise MigrationError("--database-url must be a postgresql:// URL")
    with tempfile.TemporaryDirectory() as folder:
        source = upgraded_copy(sqlite_path, Path(folder))
        try:
            problems = preflight(source)
            if problems:
                raise MigrationError("Pre-flight checks failed:\n  " + "\n  ".join(problems))
            with connect_postgres(database_url) as target:
                _apply_postgres_migrations(target)
                if not target_is_empty(target):
                    if not force:
                        raise MigrationError("The PostgreSQL database already has data. Use --force to replace it.")
                    target.execute(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE")
                print("Copying tables:")
                for table in TABLES:
                    print(f"  {table:<22} {copy_table(source, target, table)} rows")
                reset_sequences(target)
                print("Verifying:")
                problems = verify(source, target)
                if problems:
                    raise MigrationError("Verification failed:\n  " + "\n  ".join(problems))
                if dry_run:
                    target.rollback()
                    print("Dry run: verification passed, nothing was written.")
                else:
                    print("Migration committed.")
        finally:
            source.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sqlite", type=Path, default=Path(__file__).resolve().parents[1] / "data" / "packflow.db")
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    try:
        migrate(args.sqlite, args.database_url, dry_run=args.dry_run, force=args.force)
    except MigrationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
