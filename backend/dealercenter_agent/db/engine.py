from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from ..config import DBConfig

# A bare "postgresql://"/"mysql://" scheme (what Supabase and most hosted
# DBs hand you) resolves to psycopg2/MySQLdb by default — neither is in
# requirements.txt (we use psycopg3 + PyMySQL instead). Pin the driver
# explicitly here, once, so the app and Alembic can't disagree on it.
_DRIVER_OVERRIDES = {
    "postgresql://": "postgresql+psycopg://",
    "mysql://": "mysql+pymysql://",
}


def normalize_database_url(url: str) -> str:
    for bare_scheme, explicit_scheme in _DRIVER_OVERRIDES.items():
        if url.startswith(bare_scheme):
            return explicit_scheme + url[len(bare_scheme):]
    return url


# Confirmed required (2026-11-18), found via a burst load test: SQLAlchemy's
# default pool (pool_size=5 + max_overflow=10 = 15 connections) is sized
# per ENGINE — and this app builds a separate engine per module
# (app.py, webhooks.py, ingestion/jobs.py each call get_session_factory
# independently). The Supabase project's pooler (session mode) caps the
# WHOLE PROJECT at 15 concurrent connections regardless of how many of
# our own engines ask for them, so two or three engines at the default
# size blow straight through that ceiling under any real concurrency.
# Capped small enough that web process (2 engines) + worker (1 engine)
# together stay well under 15, leaving headroom for migrations/ad-hoc
# queries run alongside a live test.
_POOL_SIZE = 2
_MAX_OVERFLOW = 1


def get_engine(cfg: DBConfig) -> Engine:
    url = normalize_database_url(cfg.database_url)
    if url.startswith("sqlite"):
        # SQLite's pool classes (StaticPool/NullPool/SingletonThreadPool)
        # don't accept pool_size/max_overflow — only real DB backends do.
        return create_engine(url)
    return create_engine(url, pool_size=_POOL_SIZE, max_overflow=_MAX_OVERFLOW)


def get_session_factory(cfg: DBConfig) -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(cfg))
