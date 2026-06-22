import os
import secrets

import pytest
from sqlalchemy.orm import sessionmaker
from starlette.testclient import TestClient

# Must set env vars before app modules are first imported.
os.environ.setdefault("STORAGE_BACKEND", "sqlite")
os.environ.setdefault("BASE_URL", "http://testserver")


@pytest.fixture(autouse=True)
def _reset_globals(tmp_path, monkeypatch):
    """Reset all module-level singletons so each test gets a clean slate."""
    import pste_server.id_gen as id_gen_mod
    import pste_server.models as models_mod
    import pste_server.ratelimit as ratelimit_mod
    import pste_server.reaper as reaper_mod
    import pste_server.storage as storage_mod

    monkeypatch.setenv("STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("BASE_URL", "http://testserver")
    monkeypatch.delenv("DELETE_ON_EXPIRE", raising=False)
    monkeypatch.delenv("DELETE_ON_SINGLE_VIEW", raising=False)
    monkeypatch.delenv("DELETE_AFTER_EXPIRE", raising=False)
    monkeypatch.delenv("DELETE_AFTER_SINGLE_VIEW", raising=False)

    models_mod._engine = None
    models_mod._SessionLocal = None
    storage_mod._backend = None
    id_gen_mod._id_length_cache = None
    ratelimit_mod.reset()
    reaper_mod._paste_timers.clear()

    yield

    models_mod._engine = None
    models_mod._SessionLocal = None
    storage_mod._backend = None
    id_gen_mod._id_length_cache = None
    ratelimit_mod.reset()
    reaper_mod._paste_timers.clear()


@pytest.fixture
def db_engine(_reset_globals):
    from pste_server.models import get_engine
    return get_engine()


@pytest.fixture
def db_session(db_engine):
    Session = sessionmaker(bind=db_engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def client(monkeypatch, db_engine):
    from pste_server.main import app
    from pste_server.models import get_session

    monkeypatch.setattr("pste_server.main.start_reaper", lambda: None)
    monkeypatch.setattr("pste_server.main.stop_reaper", lambda: None)
    monkeypatch.setattr("pste_server.main.BASE_URL", "http://testserver")

    Session = sessionmaker(bind=db_engine)
    session = Session()

    def _override_session():
        try:
            yield session
        finally:
            pass

    app.dependency_overrides[get_session] = _override_session

    with TestClient(app, raise_server_exceptions=True) as c:
        yield c

    app.dependency_overrides.clear()
    session.close()


@pytest.fixture
def make_key(db_session):
    def _make(user="test", notes=None, disabled=False):
        from pste_server.models import Key
        key_val = secrets.token_urlsafe(32)
        k = Key(key=key_val, user=user, notes=notes, disabled=disabled)
        db_session.add(k)
        db_session.commit()
        return key_val
    return _make
