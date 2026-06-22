import secrets
import threading
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy.orm import sessionmaker

from pste_server.models import Key, Paste


def _seed(db, past_offset_hours=2, reason=None, deleted_at=None, single_view=False, expires_at=None):
    k = Key(key=secrets.token_urlsafe(16), user="test")
    db.add(k)
    paste_id = secrets.token_hex(3).upper()
    p = Paste(
        id=paste_id,
        created_by=k.key,
        content="test",
        size_bytes=4,
        single_view=single_view,
        expires_at=expires_at,
        deleted_at=deleted_at,
        deleted_reason=reason,
    )
    db.add(p)
    db.commit()
    return paste_id


@pytest.fixture
def scan(db_engine, monkeypatch):
    """Call _scan_and_schedule without triggering the next timer."""
    import pste_server.reaper as reaper_mod
    monkeypatch.setattr(reaper_mod, "_schedule_next", lambda: None)

    Session = sessionmaker(bind=db_engine)

    def _run():
        reaper_mod._scan_and_schedule()
        db = Session()
        try:
            db.expire_all()
            return db
        finally:
            pass  # caller closes

    return _run, Session


# ---------------------------------------------------------------------------
# _expire_paste: direct invocation
# ---------------------------------------------------------------------------

def test_expire_paste_soft_delete(db_engine, monkeypatch):
    import pste_server.config as cfg
    from pste_server.reaper import _expire_paste
    monkeypatch.setattr(cfg, "hard_delete_on_expire", lambda: False)

    Session = sessionmaker(bind=db_engine)
    db = Session()
    past = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1)
    paste_id = _seed(db, expires_at=past)
    db.close()

    _expire_paste(paste_id)

    db2 = Session()
    paste = db2.query(Paste).filter(Paste.id == paste_id).first()
    db2.close()
    assert paste.deleted_at is not None
    assert paste.deleted_reason == "expired"


def test_expire_paste_hard_delete(db_engine, monkeypatch):
    import pste_server.config as cfg
    from pste_server.reaper import _expire_paste
    monkeypatch.setattr(cfg, "hard_delete_on_expire", lambda: True)

    Session = sessionmaker(bind=db_engine)
    db = Session()
    past = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1)
    paste_id = _seed(db, expires_at=past)
    db.close()

    _expire_paste(paste_id)

    db2 = Session()
    paste = db2.query(Paste).filter(Paste.id == paste_id).first()
    db2.close()
    assert paste is None


def test_expire_paste_already_deleted_is_noop(db_engine, monkeypatch):
    import pste_server.config as cfg
    from pste_server.reaper import _expire_paste
    monkeypatch.setattr(cfg, "hard_delete_on_expire", lambda: False)

    Session = sessionmaker(bind=db_engine)
    db = Session()
    already = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
    paste_id = _seed(db, deleted_at=already, reason="expired")
    db.close()

    # Should not raise
    _expire_paste(paste_id)

    db2 = Session()
    paste = db2.query(Paste).filter(Paste.id == paste_id).first()
    db2.close()
    # Row still exists (was already soft-deleted, _expire_paste is a no-op)
    assert paste is not None
    assert paste.deleted_reason == "expired"


# ---------------------------------------------------------------------------
# Overdue expiry (in the past, caught by scan)
# ---------------------------------------------------------------------------

def test_overdue_paste_soft_deleted(scan, db_engine):
    _scan, Session = scan
    db = Session()
    past = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=2)
    paste_id = _seed(db, expires_at=past)
    db.close()

    result_db = _scan()
    paste = result_db.query(Paste).filter(Paste.id == paste_id).first()
    result_db.close()
    assert paste is not None
    assert paste.deleted_at is not None
    assert paste.deleted_reason == "expired"


def test_overdue_paste_hard_deleted_when_enabled(scan, db_engine, monkeypatch):
    import pste_server.config as cfg
    monkeypatch.setattr(cfg, "hard_delete_on_expire", lambda: True)
    _scan, Session = scan

    db = Session()
    past = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=2)
    paste_id = _seed(db, expires_at=past)
    db.close()

    result_db = _scan()
    paste = result_db.query(Paste).filter(Paste.id == paste_id).first()
    result_db.close()
    assert paste is None


# ---------------------------------------------------------------------------
# Upcoming paste: scan schedules a timer
# ---------------------------------------------------------------------------

def test_scan_schedules_upcoming_paste(scan, db_engine, monkeypatch):
    import pste_server.reaper as reaper_mod

    timers_started = []

    class FakeTimer:
        def __init__(self, delay, fn, args=()):
            self.delay = delay
            self.fn = fn
            self.args = args
            self._alive = True

        def start(self):
            if self.fn == reaper_mod._expire_paste:
                timers_started.append(self)

        def cancel(self):
            self._alive = False

        def is_alive(self):
            return self._alive

    monkeypatch.setattr("threading.Timer", FakeTimer)

    _scan, Session = scan
    db = Session()
    future = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=15)
    paste_id = _seed(db, expires_at=future)
    db.close()

    _scan()

    assert any(t.args[0] == paste_id for t in timers_started)


def test_scan_does_not_reschedule_existing_active_timer(scan, db_engine, monkeypatch):
    import pste_server.reaper as reaper_mod

    timers_started = []

    class FakeTimer:
        def __init__(self, delay, fn, args=()):
            self.delay = delay
            self.fn = fn
            self.args = args
            self._alive = True

        def start(self):
            if self.fn == reaper_mod._expire_paste:
                timers_started.append(self)

        def cancel(self):
            self._alive = False

        def is_alive(self):
            return self._alive

    monkeypatch.setattr("threading.Timer", FakeTimer)

    _scan, Session = scan
    db = Session()
    future = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=15)
    paste_id = _seed(db, expires_at=future)
    db.close()

    _scan()
    count_after_first = sum(1 for t in timers_started if t.args[0] == paste_id)

    _scan()
    count_after_second = sum(1 for t in timers_started if t.args[0] == paste_id)

    assert count_after_first == 1
    assert count_after_second == 1  # No duplicate timer


# ---------------------------------------------------------------------------
# schedule_paste_expiry
# ---------------------------------------------------------------------------

def test_schedule_paste_expiry_within_window(monkeypatch):
    import pste_server.reaper as reaper_mod

    fired = []

    class FakeTimer:
        def __init__(self, delay, fn, args=()):
            self.delay = delay
            self.fn = fn
            self.args = args
            self._alive = True

        def start(self):
            fired.append(self)

        def cancel(self):
            self._alive = False

        def is_alive(self):
            return self._alive

    monkeypatch.setattr("threading.Timer", FakeTimer)

    expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=10)
    reaper_mod.schedule_paste_expiry("TESTID", expires_at)

    assert len(fired) == 1
    assert fired[0].args[0] == "TESTID"
    assert fired[0].delay <= 10 * 60 + 1


def test_schedule_paste_expiry_outside_window(monkeypatch):
    import pste_server.reaper as reaper_mod

    fired = []

    class FakeTimer:
        def __init__(self, delay, fn, args=()):
            self._alive = True

        def start(self):
            fired.append(self)

        def cancel(self):
            self._alive = False

        def is_alive(self):
            return self._alive

    monkeypatch.setattr("threading.Timer", FakeTimer)

    expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=2)
    reaper_mod.schedule_paste_expiry("TESTID", expires_at)

    assert len(fired) == 0


def test_schedule_paste_expiry_already_expired_is_noop(monkeypatch):
    import pste_server.reaper as reaper_mod

    fired = []

    class FakeTimer:
        def __init__(self, delay, fn, args=()):
            self._alive = True

        def start(self):
            fired.append(self)

        def cancel(self):
            self._alive = False

        def is_alive(self):
            return self._alive

    monkeypatch.setattr("threading.Timer", FakeTimer)

    expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=5)
    reaper_mod.schedule_paste_expiry("TESTID", expires_at)

    assert len(fired) == 0


def test_schedule_paste_expiry_dedup(monkeypatch):
    import pste_server.reaper as reaper_mod

    fired = []

    class FakeTimer:
        def __init__(self, delay, fn, args=()):
            self.args = args
            self._alive = True

        def start(self):
            fired.append(self)

        def cancel(self):
            self._alive = False

        def is_alive(self):
            return self._alive

    monkeypatch.setattr("threading.Timer", FakeTimer)

    expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=10)
    reaper_mod.schedule_paste_expiry("TESTID", expires_at)
    reaper_mod.schedule_paste_expiry("TESTID", expires_at)  # Second call should be no-op

    assert len(fired) == 1


# ---------------------------------------------------------------------------
# start_reaper / stop_reaper
# ---------------------------------------------------------------------------

def test_stop_reaper_cancels_paste_timers(monkeypatch):
    import pste_server.reaper as reaper_mod

    cancelled = []

    class FakeTimer:
        def __init__(self, delay=0, fn=None, args=()):
            self.args = args
            self._alive = True

        def start(self):
            pass

        def cancel(self):
            cancelled.append(self)
            self._alive = False

        def is_alive(self):
            return self._alive

    monkeypatch.setattr("threading.Timer", FakeTimer)
    monkeypatch.setattr(reaper_mod, "_reaper_timer", None)

    # Manually register a fake paste timer
    t = FakeTimer()
    with reaper_mod._paste_timers_lock:
        reaper_mod._paste_timers["FAKE"] = t

    reaper_mod.stop_reaper()

    assert t in cancelled
    assert len(reaper_mod._paste_timers) == 0


def test_stop_reaper_cancels_reaper_timer(monkeypatch):
    import pste_server.reaper as reaper_mod

    cancelled = []

    class FakeTimer:
        def __init__(self, delay=0, fn=None, args=()):
            self._alive = True

        def start(self):
            pass

        def cancel(self):
            cancelled.append(self)
            self._alive = False

        def is_alive(self):
            return self._alive

    fake = FakeTimer()
    monkeypatch.setattr(reaper_mod, "_reaper_timer", fake)

    reaper_mod.stop_reaper()

    assert fake in cancelled
    assert reaper_mod._reaper_timer is None


def test_expire_paste_gcs_delete_failure_is_logged(db_engine, monkeypatch):
    """GCS delete failure after DB commit is swallowed (DB row already gone)."""
    import pste_server.config as cfg
    import pste_server.storage as storage_mod
    from pste_server.reaper import _expire_paste

    monkeypatch.setattr(cfg, "hard_delete_on_expire", lambda: True)

    fake_storage = MagicMock()
    fake_storage.delete.side_effect = Exception("GCS 500")
    monkeypatch.setattr(storage_mod, "get_storage", lambda: fake_storage)

    Session = sessionmaker(bind=db_engine)
    db = Session()
    past = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1)
    paste_id = _seed(db, expires_at=past)
    db.close()

    _expire_paste(paste_id)  # Should not raise even though GCS delete fails

    db2 = Session()
    assert db2.query(Paste).filter(Paste.id == paste_id).first() is None  # DB row removed
    db2.close()


def test_expire_paste_db_exception_is_swallowed(db_engine, monkeypatch):
    """DB error during _expire_paste is caught and logged; does not propagate."""
    import pste_server.config as cfg
    import pste_server.models as models_mod
    from pste_server.reaper import _expire_paste

    monkeypatch.setattr(cfg, "hard_delete_on_expire", lambda: False)

    original_sessionmaker = __import__("sqlalchemy.orm", fromlist=["sessionmaker"]).sessionmaker

    class BrokenSession:
        def query(self, *a, **kw):
            raise RuntimeError("DB gone")
        def rollback(self): pass
        def close(self): pass

    def broken_sessionmaker(**kw):
        class Maker:
            def __call__(self): return BrokenSession()
        return Maker()

    monkeypatch.setattr("sqlalchemy.orm.sessionmaker", broken_sessionmaker)
    # Should not raise
    _expire_paste("DOESNOTEXIST")


def test_schedule_paste_expiry_prunes_dead_timers(monkeypatch):
    """Dead timers are pruned from _paste_timers when a new one is registered."""
    import pste_server.reaper as reaper_mod

    class FakeTimer:
        def __init__(self, delay, fn, args=()):
            self.args = args
            self._alive = True

        def start(self): pass
        def cancel(self): self._alive = False
        def is_alive(self): return self._alive

    monkeypatch.setattr("threading.Timer", FakeTimer)

    dead = FakeTimer(0, None)
    dead._alive = False
    with reaper_mod._paste_timers_lock:
        reaper_mod._paste_timers["DEAD"] = dead

    expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=5)
    reaper_mod.schedule_paste_expiry("NEW", expires_at)

    with reaper_mod._paste_timers_lock:
        assert "DEAD" not in reaper_mod._paste_timers
        assert "NEW" in reaper_mod._paste_timers


def test_scan_prunes_dead_timers_before_scheduling(scan, db_engine, monkeypatch):
    """Dead timers in _paste_timers dict are pruned during scan scheduling."""
    import pste_server.reaper as reaper_mod

    class FakeTimer:
        def __init__(self, delay, fn, args=()):
            self.args = args
            self._alive = True

        def start(self): pass
        def cancel(self): self._alive = False
        def is_alive(self): return self._alive

    monkeypatch.setattr("threading.Timer", FakeTimer)

    dead = FakeTimer(0, None)
    dead._alive = False
    with reaper_mod._paste_timers_lock:
        reaper_mod._paste_timers["DEAD"] = dead

    _scan, Session = scan
    db = Session()
    future = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=10)
    paste_id = _seed(db, expires_at=future)
    db.close()

    _scan()

    with reaper_mod._paste_timers_lock:
        assert "DEAD" not in reaper_mod._paste_timers


def test_scan_exception_is_swallowed(monkeypatch):
    """Exceptions in the reaper scan try block are caught; _schedule_next still runs."""
    import pste_server.reaper as reaper_mod
    import pste_server.models as models_mod

    schedule_called = []
    monkeypatch.setattr(reaper_mod, "_schedule_next", lambda: schedule_called.append(True))

    # Make db.query raise inside the try block (get_engine is before try, query is inside)
    from unittest.mock import MagicMock
    mock_db = MagicMock()
    mock_db.query.side_effect = RuntimeError("db gone")

    class MockSessionMaker:
        def __call__(self):
            return mock_db

    monkeypatch.setattr("sqlalchemy.orm.sessionmaker", lambda bind: MockSessionMaker())
    monkeypatch.setattr(models_mod, "get_engine", lambda: MagicMock())

    reaper_mod._scan_and_schedule()  # Should not raise

    assert schedule_called  # _schedule_next still runs after exception


def test_purge_deferred_noop_when_no_config(db_engine, monkeypatch):
    """_purge_deferred returns early when both DELETE_AFTER_* are unset."""
    import pste_server.config as cfg
    from pste_server.reaper import _purge_deferred

    monkeypatch.setattr(cfg, "delete_after_expire", lambda: None)
    monkeypatch.setattr(cfg, "delete_after_single_view", lambda: None)

    Session = sessionmaker(bind=db_engine)
    db = Session()
    deleted_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=100)
    paste_id = _seed(db, deleted_at=deleted_at, reason="expired")
    db.close()

    db2 = Session()
    _purge_deferred(db2, datetime.now(timezone.utc).replace(tzinfo=None))
    db2.close()

    db3 = Session()
    assert db3.query(Paste).filter(Paste.id == paste_id).first() is not None
    db3.close()


def test_schedule_next_creates_30min_timer(monkeypatch):
    """_schedule_next registers a 30-minute threading.Timer."""
    import pste_server.reaper as reaper_mod

    created = []

    class FakeTimer:
        def __init__(self, delay, fn, args=()):
            self.delay = delay
            self._alive = True
            created.append(self)

        def start(self): pass
        def cancel(self): self._alive = False
        def is_alive(self): return self._alive

    monkeypatch.setattr("threading.Timer", FakeTimer)
    reaper_mod._schedule_next()

    assert len(created) >= 1
    timer = reaper_mod._reaper_timer
    assert timer.delay == 30 * 60


def test_start_reaper_does_not_block(monkeypatch):
    import pste_server.reaper as reaper_mod

    ran = []

    def fake_scan():
        ran.append(True)

    monkeypatch.setattr(reaper_mod, "_scan_and_schedule", fake_scan)

    reaper_mod.start_reaper()

    # Give the daemon thread a moment to fire
    import time
    for _ in range(20):
        if ran:
            break
        time.sleep(0.05)

    assert ran, "start_reaper should run _scan_and_schedule in a background thread"


# ---------------------------------------------------------------------------
# _purge_deferred
# ---------------------------------------------------------------------------

def test_purge_deferred_expired_past_threshold(db_engine, monkeypatch):
    import pste_server.config as cfg
    from pste_server.reaper import _purge_deferred

    monkeypatch.setattr(cfg, "delete_after_expire", lambda: timedelta(days=7))
    monkeypatch.setattr(cfg, "delete_after_single_view", lambda: None)

    Session = sessionmaker(bind=db_engine)
    db = Session()
    deleted_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=8)
    paste_id = _seed(db, deleted_at=deleted_at, reason="expired")
    db.close()

    db2 = Session()
    _purge_deferred(db2, datetime.now(timezone.utc).replace(tzinfo=None))
    db2.close()

    db3 = Session()
    paste = db3.query(Paste).filter(Paste.id == paste_id).first()
    db3.close()
    assert paste is None


def test_purge_deferred_not_yet_due(db_engine, monkeypatch):
    import pste_server.config as cfg
    from pste_server.reaper import _purge_deferred

    monkeypatch.setattr(cfg, "delete_after_expire", lambda: timedelta(days=30))
    monkeypatch.setattr(cfg, "delete_after_single_view", lambda: None)

    Session = sessionmaker(bind=db_engine)
    db = Session()
    deleted_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)
    paste_id = _seed(db, deleted_at=deleted_at, reason="expired")
    db.close()

    db2 = Session()
    _purge_deferred(db2, datetime.now(timezone.utc).replace(tzinfo=None))
    db2.close()

    db3 = Session()
    paste = db3.query(Paste).filter(Paste.id == paste_id).first()
    db3.close()
    assert paste is not None


def test_purge_deferred_single_view(db_engine, monkeypatch):
    import pste_server.config as cfg
    from pste_server.reaper import _purge_deferred

    monkeypatch.setattr(cfg, "delete_after_expire", lambda: None)
    monkeypatch.setattr(cfg, "delete_after_single_view", lambda: timedelta(days=7))

    Session = sessionmaker(bind=db_engine)
    db = Session()
    deleted_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=8)
    paste_id = _seed(db, deleted_at=deleted_at, reason="single_view", single_view=True)
    db.close()

    db2 = Session()
    _purge_deferred(db2, datetime.now(timezone.utc).replace(tzinfo=None))
    db2.close()

    db3 = Session()
    paste = db3.query(Paste).filter(Paste.id == paste_id).first()
    db3.close()
    assert paste is None


# ---------------------------------------------------------------------------
# Config: _duration_env parsing
# ---------------------------------------------------------------------------

def test_duration_env_hours(monkeypatch):
    from pste_server.config import _duration_env
    monkeypatch.setenv("DELETE_AFTER_EXPIRE", "12H")
    result = _duration_env("DELETE_AFTER_EXPIRE")
    assert result == timedelta(hours=12)


def test_duration_env_days(monkeypatch):
    from pste_server.config import _duration_env
    monkeypatch.setenv("DELETE_AFTER_EXPIRE", "7D")
    result = _duration_env("DELETE_AFTER_EXPIRE")
    assert result == timedelta(days=7)


def test_duration_env_weeks(monkeypatch):
    from pste_server.config import _duration_env
    monkeypatch.setenv("DELETE_AFTER_EXPIRE", "2W")
    result = _duration_env("DELETE_AFTER_EXPIRE")
    assert result == timedelta(weeks=2)


def test_duration_env_minutes(monkeypatch):
    from pste_server.config import _duration_env
    monkeypatch.setenv("DELETE_AFTER_EXPIRE", "3M")
    result = _duration_env("DELETE_AFTER_EXPIRE")
    assert result == timedelta(seconds=180)


def test_duration_env_lowercase(monkeypatch):
    from pste_server.config import _duration_env
    monkeypatch.setenv("DELETE_AFTER_EXPIRE", "6h")
    result = _duration_env("DELETE_AFTER_EXPIRE")
    assert result == timedelta(hours=6)


def test_duration_env_empty_returns_none(monkeypatch):
    from pste_server.config import _duration_env
    monkeypatch.setenv("DELETE_AFTER_EXPIRE", "")
    assert _duration_env("DELETE_AFTER_EXPIRE") is None


def test_duration_env_invalid_raises(monkeypatch):
    from pste_server.config import _duration_env
    monkeypatch.setenv("DELETE_AFTER_EXPIRE", "30s")  # seconds not supported
    with pytest.raises(ValueError, match="Invalid"):
        _duration_env("DELETE_AFTER_EXPIRE")


def test_purge_deferred_runs_even_when_delete_on_is_true(db_engine, monkeypatch):
    """Regression: DELETE_AFTER_* must not be skipped when DELETE_ON_* is true.

    Old code did: after_expire = None if hard_delete_on_expire() else delete_after_expire()
    That meant existing soft-deleted rows were never purged after enabling DELETE_ON_EXPIRE.
    """
    import pste_server.config as cfg
    from pste_server.reaper import _purge_deferred

    monkeypatch.setattr(cfg, "hard_delete_on_expire", lambda: True)
    monkeypatch.setattr(cfg, "delete_after_expire", lambda: timedelta(days=7))
    monkeypatch.setattr(cfg, "delete_after_single_view", lambda: None)

    Session = sessionmaker(bind=db_engine)
    db = Session()
    # A soft-deleted row that accumulated before DELETE_ON_EXPIRE was turned on
    deleted_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=8)
    paste_id = _seed(db, deleted_at=deleted_at, reason="expired")
    db.close()

    db2 = Session()
    _purge_deferred(db2, datetime.now(timezone.utc).replace(tzinfo=None))
    db2.close()

    db3 = Session()
    paste = db3.query(Paste).filter(Paste.id == paste_id).first()
    db3.close()
    assert paste is None, "Stale soft-deleted row must be purged even when DELETE_ON_EXPIRE=true"
