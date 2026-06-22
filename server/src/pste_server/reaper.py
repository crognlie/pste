import logging
import threading
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

_reaper_timer: threading.Timer | None = None
_paste_timers: dict[str, threading.Timer] = {}
_paste_timers_lock = threading.Lock()

# Schedule a per-paste timer at creation time for pastes expiring sooner than
# this window, so they aren't delayed up to 30 min waiting for the next scan.
_EARLY_SCHEDULE_WINDOW = timedelta(minutes=45)


def _expire_paste(paste_id: str):
    from pste_server.config import hard_delete_on_expire
    from pste_server.models import Paste, get_engine
    from sqlalchemy.orm import sessionmaker

    Session = sessionmaker(bind=get_engine())
    db = Session()
    try:
        paste = db.query(Paste).filter(
            Paste.id == paste_id,
            Paste.deleted_at.is_(None),
        ).first()
        if paste:
            if hard_delete_on_expire():
                from pste_server.storage import get_storage
                gcs_key = paste.gcs_key
                db.delete(paste)
                db.commit()
                logger.info("Hard-deleted expired paste %s", paste_id)
                try:
                    get_storage().delete(paste_id, gcs_key)
                except Exception:
                    logger.exception("GCS delete failed for expired paste %s (DB row already removed)", paste_id)
                return
            else:
                paste.deleted_at = datetime.now(timezone.utc).replace(tzinfo=None)
                paste.deleted_reason = "expired"
                logger.info("Soft-deleted expired paste %s", paste_id)
            db.commit()
    except Exception:
        logger.exception("Error expiring paste %s", paste_id)
        db.rollback()
    finally:
        db.close()


def _schedule_timer(paste_id: str, delay: float):
    """Create, start, and register a per-paste expiry timer. Caller holds _paste_timers_lock."""
    t = threading.Timer(delay, _expire_paste, args=[paste_id])
    t.daemon = True
    t.start()
    _paste_timers[paste_id] = t
    logger.debug("Scheduled expiry for %s in %.1fs", paste_id, delay)


def schedule_paste_expiry(paste_id: str, expires_at: datetime):
    """Schedule a per-paste expiry timer if the paste expires within the early window.

    Called at paste creation time to ensure pastes with short expiry times are
    handled promptly, rather than waiting up to 30 minutes for the next scan.
    No-op if the paste expires beyond the window or a timer is already active.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    time_until = expires_at - now
    if time_until <= timedelta(0) or time_until > _EARLY_SCHEDULE_WINDOW:
        return
    delay = time_until.total_seconds()
    with _paste_timers_lock:
        existing = _paste_timers.get(paste_id)
        if existing and existing.is_alive():
            return
        dead = [pid for pid, t in _paste_timers.items() if not t.is_alive()]
        for pid in dead:
            del _paste_timers[pid]
        _schedule_timer(paste_id, delay)


def _scan_and_schedule():
    from pste_server.models import Paste, get_engine
    from sqlalchemy.orm import sessionmaker

    Session = sessionmaker(bind=get_engine())
    db = Session()
    try:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        window_end = now + timedelta(minutes=30)
        upcoming = db.query(Paste).filter(
            Paste.expires_at.isnot(None),
            Paste.expires_at > now,
            Paste.expires_at <= window_end,
            Paste.deleted_at.is_(None),
        ).all()

        for paste in upcoming:
            delay = (paste.expires_at - now).total_seconds()
            delay = max(0.0, delay)
            with _paste_timers_lock:
                existing = _paste_timers.get(paste.id)
                if existing and existing.is_alive():
                    continue
                dead = [pid for pid, t in _paste_timers.items() if not t.is_alive()]
                for pid in dead:
                    del _paste_timers[pid]
                _schedule_timer(paste.id, delay)

        # Also immediately expire anything past due that wasn't caught
        from pste_server.config import hard_delete_on_expire
        from pste_server.storage import get_storage
        overdue = db.query(Paste).filter(
            Paste.expires_at.isnot(None),
            Paste.expires_at <= now,
            Paste.deleted_at.is_(None),
        ).all()
        overdue_gcs = []
        for paste in overdue:
            if hard_delete_on_expire():
                overdue_gcs.append((paste.id, paste.gcs_key))
                db.delete(paste)
                logger.info("Hard-deleted overdue paste %s", paste.id)
            else:
                paste.deleted_at = now
                paste.deleted_reason = "expired"
                logger.info("Soft-deleted overdue paste %s", paste.id)
        if overdue:
            db.commit()
            for pid, gkey in overdue_gcs:
                get_storage().delete(pid, gkey)

        _purge_deferred(db, now)

        total = db.query(Paste).count()
        from pste_server.id_gen import bump_id_length_if_needed
        bump_id_length_if_needed(total, db)
    except Exception:
        logger.exception("Error in reaper scan")
        db.rollback()
    finally:
        db.close()

    _schedule_next()


def _purge_deferred(db, now: datetime):
    """Hard-delete soft-deleted records that have aged past DELETE_AFTER_* thresholds."""
    from pste_server.config import delete_after_expire, delete_after_single_view

    after_expire = delete_after_expire()
    after_sv = delete_after_single_view()

    if not after_expire and not after_sv:
        return

    reasons = []
    if after_expire:
        reasons.append(("expired", after_expire))
    if after_sv:
        reasons.append(("single_view", after_sv))

    from pste_server.models import Paste
    from pste_server.storage import get_storage

    total = 0
    all_gcs = []
    storage = get_storage()
    for reason, delta in reasons:
        cutoff = now - delta
        stale = db.query(Paste).filter(
            Paste.deleted_reason == reason,
            Paste.deleted_at.isnot(None),
            Paste.deleted_at <= cutoff,
        ).all()
        for paste in stale:
            all_gcs.append((paste.id, paste.gcs_key))
            db.delete(paste)
            logger.info("Purged %s paste %s (deleted_at=%s, cutoff=%s)", reason, paste.id, paste.deleted_at, cutoff)
        total += len(stale)

    if total:
        db.commit()
        for pid, gkey in all_gcs:
            storage.delete(pid, gkey)


def _schedule_next():
    global _reaper_timer
    _reaper_timer = threading.Timer(30 * 60, _scan_and_schedule)
    _reaper_timer.daemon = True
    _reaper_timer.start()


def start_reaper():
    t = threading.Thread(target=_scan_and_schedule, daemon=True)
    t.start()


def stop_reaper():
    global _reaper_timer
    if _reaper_timer:
        _reaper_timer.cancel()
        _reaper_timer = None
    with _paste_timers_lock:
        for t in _paste_timers.values():
            t.cancel()
        _paste_timers.clear()
    # Note: a timer registered between the last append and this lock acquisition
    # won't be cancelled here, but all timers are daemon threads so process
    # exit is clean regardless.
