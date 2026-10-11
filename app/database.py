"""Database engine and session setup (SQLAlchemy 2.0, synchronous)."""
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    """Parent class of all ORM models."""


def make_engine(database_url: str) -> Engine:
    is_sqlite = database_url.startswith("sqlite")

    # The API handles requests in worker threads and the background job runs in
    # another thread. SQLite's driver refuses cross-thread use by default; that
    # is safe to switch off here because every thread opens its OWN session.
    connect_args = {"check_same_thread": False} if is_sqlite else {}
    engine = create_engine(database_url, connect_args=connect_args)

    if is_sqlite:

        @event.listens_for(engine, "connect")
        def _configure_sqlite(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            # WAL lets status requests read while the background job writes.
            cursor.execute("PRAGMA journal_mode=WAL")
            # Wait up to 5s for a lock instead of failing with "database is locked".
            cursor.execute("PRAGMA busy_timeout=5000")
            # SQLite does not enforce foreign keys unless asked to.
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def make_session_factory(engine: Engine) -> sessionmaker:
    # expire_on_commit=False: objects stay readable after commit(), so the
    # worker can commit after every certificate without re-querying the job.
    return sessionmaker(bind=engine, expire_on_commit=False)