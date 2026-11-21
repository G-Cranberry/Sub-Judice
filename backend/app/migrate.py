"""Tiny, safe upgrade for databases created by v0.1 (no Alembic needed for the prototype).
Adds the new columns on `cases` if they are missing. Never drops or rewrites data."""
from sqlalchemy import inspect, text

ADDED_COLUMNS = {
    "cases": {"custody_head": "VARCHAR(64)", "custody_seq": "INTEGER NOT NULL DEFAULT 0",
               "access_token_hash": "VARCHAR(64)",
               "source": "VARCHAR(20) NOT NULL DEFAULT 'user'", "submitted_by": "VARCHAR(200)"},
}


def upgrade(engine) -> list[str]:
    done = []
    insp = inspect(engine)
    with engine.begin() as conn:
        for table, cols in ADDED_COLUMNS.items():
            if not insp.has_table(table):
                continue
            existing = {c["name"] for c in insp.get_columns(table)}
            for name, ddl in cols.items():
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
                    done.append(f"{table}.{name}")
    return done
