"""Migration runner: applies storage/migrations/*.sql in order, idempotently."""
from __future__ import annotations

import os
from pathlib import Path

import psycopg

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def apply_migrations(dsn: str | None = None, migrations_dir: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply pending migrations; returns list of applied filenames."""
    applied: list[str] = []
    dsn = dsn or os.environ.get("DATABASE_URL", "postgresql://app:app@localhost:5433/qo")
    with psycopg.connect(dsn, autocommit=False, connect_timeout=3) as conn, conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "name TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        cur.execute("SELECT name FROM schema_migrations")
        done = {row[0] for row in cur.fetchall()}
        for path in sorted(migrations_dir.glob("*.sql")):
            if path.name in done:
                continue
            cur.execute(path.read_text(encoding="utf-8"))
            cur.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (path.name,))
            applied.append(path.name)
        conn.commit()
    return applied


def main() -> int:
    dsn = os.environ.get("DATABASE_URL", "postgresql://app:app@localhost:5433/qo")
    applied = apply_migrations(dsn)
    for name in applied:
        print(f"applied {name}")
    print(f"migrations up to date ({len(applied)} applied)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
