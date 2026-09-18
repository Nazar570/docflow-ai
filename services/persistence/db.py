from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from services.settings import Settings


def create_db_engine(settings: Settings) -> Engine:
    return create_engine(settings.database_url, pool_pre_ping=True)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(
        bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
    )


@contextmanager
def session_scope(
    session_factory: sessionmaker[Session],
) -> Generator[Session, None, None]:
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
