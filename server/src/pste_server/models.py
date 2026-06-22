import os
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Integer, String, Text, create_engine
)
from sqlalchemy.orm import DeclarativeBase, sessionmaker

_engine = None
_SessionLocal = None


class Base(DeclarativeBase):
    pass


class ServerState(Base):
    __tablename__ = "server_state"

    key = Column(String, primary_key=True)
    value = Column(String, nullable=False)


class Key(Base):
    __tablename__ = "keys"

    key = Column(String, primary_key=True)
    user = Column(String)
    notes = Column(Text)
    disabled = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class Paste(Base):
    __tablename__ = "pastes"

    id = Column(String, primary_key=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    created_by = Column(String, ForeignKey("keys.key"))
    expires_at = Column(DateTime)
    single_view = Column(Boolean, default=False, nullable=False)
    deleted_at = Column(DateTime)
    deleted_reason = Column(String)
    lang = Column(String)
    size_bytes = Column(Integer)
    content = Column(Text)
    gcs_key = Column(String)


def get_engine():
    global _engine
    if _engine is None:
        backend = os.environ.get("STORAGE_BACKEND", "sqlite")
        if backend == "sqlite":
            path = os.environ.get("SQLITE_PATH", "./data/pste.db")
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            _engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
        elif backend in ("postgresql", "gcs"):
            url = os.environ["DATABASE_URL"]
            _engine = create_engine(url, pool_pre_ping=True)
        else:
            raise ValueError(f"Unknown STORAGE_BACKEND: {backend}")
        Base.metadata.create_all(_engine)
    return _engine


def get_session():
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=get_engine())
    db = _SessionLocal()
    try:
        yield db
    finally:
        db.close()
