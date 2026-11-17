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


def get_engine(cfg: DBConfig) -> Engine:
    return create_engine(normalize_database_url(cfg.database_url))


def get_session_factory(cfg: DBConfig) -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(cfg))
