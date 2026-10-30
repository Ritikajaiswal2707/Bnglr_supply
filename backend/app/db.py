"""Database engine and session helpers.

SQLite is used for the single-instance prototype. The only SQLite-specific code is
the begin hook below, which lets the assignment path request BEGIN IMMEDIATE so two
coordinators cannot both pass the availability check. On Postgres the same code path
uses SELECT ... FOR UPDATE on the provider row instead.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


_engine: Engine | None = None
_factory: sessionmaker | None = None
_locking_factory: sessionmaker | None = None


def make_engine(url: str) -> Engine:
    kwargs: dict = {"pool_pre_ping": True}
    is_sqlite = url.startswith("sqlite")
    if is_sqlite:
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
        path = url.split("///", 1)[-1]
        if path and path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    engine = create_engine(url, **kwargs)
    if is_sqlite:

        @event.listens_for(engine, "connect")
        def _on_connect(dbapi_conn, _record):  # pragma: no cover - trivial
            dbapi_conn.isolation_level = None  # we emit BEGIN ourselves
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.close()

        @event.listens_for(engine, "begin")
        def _on_begin(conn):
            mode = conn.get_execution_options().get("sqlite_begin", "DEFERRED")
            conn.exec_driver_sql(f"BEGIN {mode}")

    return engine


def init_engine(url: str) -> Engine:
    global _engine, _factory, _locking_factory
    if _engine is not None:
        _engine.dispose()
    _engine = make_engine(url)
    _factory = sessionmaker(bind=_engine, expire_on_commit=False)
    if url.startswith("sqlite"):
        _locking_factory = sessionmaker(bind=_engine.execution_options(sqlite_begin="IMMEDIATE"), expire_on_commit=False)
    else:
        _locking_factory = _factory
    return _engine


def get_engine() -> Engine:
    assert _engine is not None, "init_engine() has not been called"
    return _engine


def new_session() -> Session:
    assert _factory is not None, "init_engine() has not been called"
    return _factory()


def new_locking_session() -> Session:
    """Session whose transaction takes the write lock up front (SQLite) so that
    check-then-write sequences are serialised."""
    assert _locking_factory is not None, "init_engine() has not been called"
    return _locking_factory()


def get_db() -> Iterator[Session]:
    db = new_session()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope(locking: bool = False) -> Iterator[Session]:
    db = new_locking_session() if locking else new_session()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
