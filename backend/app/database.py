import os

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

_DEFAULT_DB = "sqlite:///./subjudice.db"
if not os.getenv("DATABASE_URL") and os.path.exists("./casetrace.db") and not os.path.exists("./subjudice.db"):
    _DEFAULT_DB = "sqlite:///./casetrace.db"  # project was renamed from CaseTrace: keep using the existing database
DATABASE_URL = os.getenv("DATABASE_URL", _DEFAULT_DB)
if DATABASE_URL.startswith("postgres://"):  # Heroku/Render style URL
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

if DATABASE_URL.startswith("postgresql://"):
    # Plain postgresql:// means "psycopg2" on older SQLAlchemy but "psycopg (v3)" on newer ones.
    # Pick whichever driver is actually installed so the same URL works everywhere.
    import importlib.util

    for _mod, _driver in (("psycopg2", "psycopg2"), ("psycopg", "psycopg")):
        if importlib.util.find_spec(_mod):
            DATABASE_URL = DATABASE_URL.replace("postgresql://", f"postgresql+{_driver}://", 1)
            break

IS_SQLITE = DATABASE_URL.startswith("sqlite")
# SQLite: wait up to 30s for a write lock instead of failing instantly with "database is locked".
connect_args = {"check_same_thread": False, "timeout": 30} if IS_SQLITE else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=not IS_SQLITE)

if IS_SQLITE:

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")      # SQLite ignores FKs unless asked
        cur.execute("PRAGMA journal_mode=WAL")     # readers don't block the writer
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
