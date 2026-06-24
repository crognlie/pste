import re
from datetime import datetime, timedelta, timezone

import pytest


def _future_ts(days=1):
    dt = datetime.now(timezone.utc) + timedelta(days=days)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _post(client, key, content="hello", **extra):
    """POST via API (Authorization header) — returns plain text URL."""
    data = {"pste": content, **extra}
    return client.post(
        "/",
        data=data,
        headers={"Authorization": f"Bearer {key}"},
    )


def _web_post(client, key, content="hello", **extra):
    """POST via web form (pste_key field, auto_detect=1 implicit) — returns HTML."""
    data = {"pste": content, "pste_key": key, "auto_detect": "1", **extra}
    return client.post("/", data=data)


def _hrefs(html: str) -> list[str]:
    """Return all href values from an HTML result page."""
    return re.findall(r'href="([^"]+)"', html)


def _create_paste(client, key, content="hello", **extra):
    """Create a paste via API and return the paste ID (strips any ?lang)."""
    r = _post(client, key, content=content, **extra)
    assert r.status_code == 200
    return r.text.strip().split("?")[0].split("/")[-1]


# ---------------------------------------------------------------------------
# GET /
# ---------------------------------------------------------------------------

def test_index_returns_help(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "pste(1)" in r.text


def test_index_with_key_shows_form(client, make_key):
    key = make_key()
    r = client.get(f"/?key={key}")
    assert r.status_code == 200
    assert 'name="pste_key"' in r.text
    assert key in r.text


def test_index_form_has_auto_detect_field(client, make_key):
    key = make_key()
    r = client.get(f"/?key={key}")
    assert 'name="auto_detect"' in r.text


def test_index_form_has_expiry_fields(client, make_key):
    key = make_key()
    r = client.get(f"/?key={key}")
    assert 'name="expires_in_n"' in r.text
    assert 'name="expires_in_unit"' in r.text


def test_index_form_has_lang_dropdown(client, make_key):
    key = make_key()
    r = client.get(f"/?key={key}")
    assert 'name="lang"' in r.text
    assert "python" in r.text.lower()


# ---------------------------------------------------------------------------
# POST / — auth
# ---------------------------------------------------------------------------

def test_post_no_auth_401(client):
    r = client.post("/", data={"pste": "hello"})
    assert r.status_code == 401


def test_post_invalid_key_401(client):
    r = client.post("/", data={"pste": "hello"}, headers={"Authorization": "Bearer notakey"})
    assert r.status_code == 401


def test_post_disabled_key_403(client, make_key):
    key = make_key(disabled=True)
    r = client.post("/", data={"pste": "hello"}, headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 403


def test_post_web_form_key_field(client, make_key):
    key = make_key()
    r = client.post("/", data={"pste": "hello", "pste_key": key})
    assert r.status_code == 200
    assert "http://testserver/" in r.text


# ---------------------------------------------------------------------------
# POST / — validation
# ---------------------------------------------------------------------------

def test_post_unknown_field_422(client, make_key):
    key = make_key()
    r = _post(client, key, badfield="x")
    assert r.status_code == 422


def test_post_invalid_lang_422(client, make_key):
    key = make_key()
    r = _post(client, key, lang="notareallanguage12345")
    assert r.status_code == 422


def test_post_past_expires_at_422(client, make_key):
    key = make_key()
    past = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    r = _post(client, key, expires_at=past)
    assert r.status_code == 422


def test_post_expires_in_zero_422(client, make_key):
    key = make_key()
    r = _post(client, key, expires_in_n="0", expires_in_unit="D")
    assert r.status_code == 422


def test_post_expires_in_invalid_unit_422(client, make_key):
    key = make_key()
    r = _post(client, key, expires_in_n="7", expires_in_unit="X")
    assert r.status_code == 422


def test_post_oversized_422(client, make_key, monkeypatch):
    monkeypatch.setenv("MAX_PASTE_BYTES", "10")
    import pste_server.validation as v
    monkeypatch.setattr(v, "MAX_PASTE_BYTES", 10)
    key = make_key()
    r = _post(client, key, content="x" * 11)
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# POST / — API response (plain text URL)
# ---------------------------------------------------------------------------

def test_post_success_returns_plain_url(client, make_key):
    key = make_key()
    r = _post(client, key)
    assert r.status_code == 200
    url = r.text.strip()
    assert url.startswith("http://testserver/")
    paste_id = url.split("/")[-1]
    assert len(paste_id) == 6


def test_post_explicit_lang_api_returns_highlighted_url(client, make_key, db_session):
    from pste_server.models import Paste
    key = make_key()
    r = _post(client, key, lang="python")
    assert r.status_code == 200
    assert r.text.strip().endswith("?python")
    paste_id = r.text.strip().split("?")[0].split("/")[-1]
    assert db_session.query(Paste).filter(Paste.id == paste_id).first().lang == "python"


def test_post_auto_detect_api_detects_python(client, make_key):
    key = make_key()
    r = client.post(
        "/",
        data={"pste": "import os\ndef main():\n    pass\n", "auto_detect": "1"},
        headers={"Authorization": f"Bearer {key}"},
    )
    assert r.status_code == 200
    assert "?" in r.text  # detected lang appended as ?lang


def test_post_no_lang_no_autodetect_returns_plain_url(client, make_key):
    key = make_key()
    r = _post(client, key, content="print('hello')")
    assert r.status_code == 200
    assert "?" not in r.text.strip()


def test_post_with_single_view(client, make_key, db_session):
    from pste_server.models import Paste
    key = make_key()
    r = _post(client, key, single_view="1")
    assert r.status_code == 200
    paste_id = r.text.strip().split("/")[-1]
    assert db_session.query(Paste).filter(Paste.id == paste_id).first().single_view is True


def test_post_with_expires_at(client, make_key, db_session):
    from pste_server.models import Paste
    key = make_key()
    ts = _future_ts(7)
    r = _post(client, key, expires_at=ts)
    assert r.status_code == 200
    paste_id = r.text.strip().split("/")[-1]
    assert db_session.query(Paste).filter(Paste.id == paste_id).first().expires_at is not None


def test_post_expires_in_fields_sets_expires_at(client, make_key, db_session):
    from pste_server.models import Paste
    key = make_key()
    r = _post(client, key, expires_in_n="7", expires_in_unit="D")
    assert r.status_code == 200
    paste_id = r.text.strip().split("/")[-1]
    assert db_session.query(Paste).filter(Paste.id == paste_id).first().expires_at is not None


# ---------------------------------------------------------------------------
# POST / — web form response (HTML)
# ---------------------------------------------------------------------------

def test_web_form_no_lang_returns_html_plain_url(client, make_key):
    """Web form, no lang selected, plain text → single plain URL."""
    key = make_key()
    r = _web_post(client, key, content="just plain text with nothing identifiable at all")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    urls = _hrefs(r.text)
    assert len(urls) == 1
    assert "?" not in urls[0]


def test_web_form_auto_detect_python_returns_two_urls(client, make_key):
    """Web form, auto_detect=1, Python code → plain URL + highlighted URL."""
    key = make_key()
    r = _web_post(client, key, content="import os\ndef main():\n    return 0\n")
    assert r.status_code == 200
    urls = _hrefs(r.text)
    assert len(urls) == 2
    plain = [u for u in urls if "?" not in u]
    highlighted = [u for u in urls if "?" in u]
    assert len(plain) == 1
    assert len(highlighted) == 1


def test_web_form_explicit_lang_returns_single_highlighted_url(client, make_key):
    """Web form, explicit lang=python → only the ?python URL, no plain URL."""
    key = make_key()
    r = _web_post(client, key, content="print('hi')", lang="python")
    assert r.status_code == 200
    urls = _hrefs(r.text)
    assert len(urls) == 1
    assert "?python" in urls[0]


def test_web_form_expires_in_fields_set_expiry(client, make_key, db_session):
    from pste_server.models import Paste
    key = make_key()
    r = _web_post(client, key, content="expires soon", expires_in_n="3", expires_in_unit="D")
    assert r.status_code == 200
    url = _hrefs(r.text)[0]
    paste_id = url.split("/")[-1].split("?")[0]
    db_session.expire_all()
    assert db_session.query(Paste).filter(Paste.id == paste_id).first().expires_at is not None


# ---------------------------------------------------------------------------
# GET /<id> — plain text
# ---------------------------------------------------------------------------

def test_get_unknown_404(client):
    r = client.get("/ZZZZZZ")
    assert r.status_code == 404


def test_get_soft_deleted_404(client, make_key, db_session):
    from pste_server.models import Paste
    key = make_key()
    paste_id = _create_paste(client, key)
    paste = db_session.query(Paste).filter(Paste.id == paste_id).first()
    paste.deleted_at = datetime.now(timezone.utc).replace(tzinfo=None)
    paste.deleted_reason = "expired"
    db_session.commit()
    assert client.get(f"/{paste_id}").status_code == 404


def test_get_returns_content_and_header(client, make_key):
    key = make_key()
    paste_id = _create_paste(client, key, content="hello world")
    r = client.get(f"/{paste_id}")
    assert r.status_code == 200
    assert "hello world" in r.text
    assert "X-Pste-Created" in r.headers
    assert "text/plain" in r.headers["content-type"]


def test_get_case_insensitive(client, make_key):
    key = make_key()
    paste_id = _create_paste(client, key, content="case test")
    r = client.get(f"/{paste_id.lower()}")
    assert r.status_code == 200
    assert "case test" in r.text


def test_get_no_query_always_returns_plain(client, make_key):
    """Stored lang never auto-renders; bare GET is always plain text."""
    key = make_key()
    paste_id = _create_paste(client, key, content="print('hi')", lang="python")
    r = client.get(f"/{paste_id}")
    assert r.status_code == 200
    assert "text/plain" in r.headers["content-type"]
    assert "<html" not in r.text


def test_get_none_query_returns_plain(client, make_key):
    key = make_key()
    paste_id = _create_paste(client, key, content="raw", lang="python")
    r = client.get(f"/{paste_id}?none")
    assert r.status_code == 200
    assert "text/plain" in r.headers["content-type"]


# ---------------------------------------------------------------------------
# GET /<id>?<lang> — highlighted HTML
# ---------------------------------------------------------------------------

def test_get_lang_query_returns_html(client, make_key):
    key = make_key()
    paste_id = _create_paste(client, key, content="print('hi')")
    r = client.get(f"/{paste_id}?python")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_get_lang_query_has_copy_button(client, make_key):
    key = make_key()
    paste_id = _create_paste(client, key, content="print('hi')")
    r = client.get(f"/{paste_id}?python")
    assert r.status_code == 200
    assert "clipboard" in r.text.lower()
    assert "copy" in r.text.lower()


def test_get_lang_query_linenos_userselect_none(client, make_key):
    key = make_key()
    paste_id = _create_paste(client, key, content="line1\nline2\n")
    r = client.get(f"/{paste_id}?text")
    assert r.status_code == 200
    assert "user-select: none" in r.text


def test_get_lang_query_has_table_linenos(client, make_key):
    key = make_key()
    paste_id = _create_paste(client, key, content="a\nb\nc\n")
    r = client.get(f"/{paste_id}?text")
    assert r.status_code == 200
    assert 'class="linenos"' in r.text
    assert 'class="code"' in r.text


def test_get_unknown_lang_query_falls_back_to_text(client, make_key):
    key = make_key()
    paste_id = _create_paste(client, key, content="hello")
    r = client.get(f"/{paste_id}?notareallang99")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_highlight_style_env_var(client, make_key, monkeypatch):
    from pygments.formatters import HtmlFormatter
    import pste_server.main as m
    dark_fmt = HtmlFormatter(style="github-dark", lineanchors="n", linenos="table")
    monkeypatch.setattr(m, "_formatter", dark_fmt)
    monkeypatch.setattr(m, "_style_defs", dark_fmt.get_style_defs(".highlight"))
    key = make_key()
    paste_id = _create_paste(client, key, content="print('hi')")
    r = client.get(f"/{paste_id}?python")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    # github-dark has a dark background
    assert "#0d1117" in r.text  # github-dark bg color


def test_dark_mode_env_uses_github_dark(monkeypatch):
    monkeypatch.setenv("DARK_MODE", "true")
    monkeypatch.delenv("HIGHLIGHT_STYLE", raising=False)
    import importlib
    import pste_server.main as m
    importlib.reload(m)
    assert m.HIGHLIGHT_STYLE == "github-dark"
    importlib.reload(m)  # restore defaults after test


# ---------------------------------------------------------------------------
# Single-view
# ---------------------------------------------------------------------------

def test_single_view_first_get_succeeds_second_404(client, make_key):
    key = make_key()
    paste_id = _create_paste(client, key, content="secret", single_view="1")
    r1 = client.get(f"/{paste_id}")
    assert r1.status_code == 200
    assert "secret" in r1.text
    assert client.get(f"/{paste_id}").status_code == 404


def test_single_view_soft_deleted_by_default(client, make_key, db_session):
    from pste_server.models import Paste
    key = make_key()
    paste_id = _create_paste(client, key, single_view="1")
    client.get(f"/{paste_id}")
    db_session.expire_all()
    paste = db_session.query(Paste).filter(Paste.id == paste_id).first()
    assert paste is not None
    assert paste.deleted_at is not None
    assert paste.deleted_reason == "single_view"


def test_single_view_hard_delete_when_enabled(client, make_key, db_session, monkeypatch):
    from pste_server.models import Paste
    import pste_server.main as m
    monkeypatch.setattr(m, "hard_delete_on_single_view", lambda: True)
    key = make_key()
    paste_id = _create_paste(client, key, single_view="1")
    client.get(f"/{paste_id}")
    db_session.expire_all()
    assert db_session.query(Paste).filter(Paste.id == paste_id).first() is None


def test_create_paste_schedules_expiry_timer_for_short_ttl(client, make_key, monkeypatch):
    """Pastes expiring within 45 min get schedule_paste_expiry called at creation."""
    import pste_server.main as main_mod
    scheduled = []
    monkeypatch.setattr(main_mod, "schedule_paste_expiry", lambda pid, exp: scheduled.append(pid))

    key = make_key()
    r = client.post(
        "/",
        data={"pste": "hello", "expires_in_n": "10", "expires_in_unit": "M"},
        headers={"Authorization": f"Bearer {key}"},
    )
    assert r.status_code == 200
    assert len(scheduled) == 1


def test_create_paste_no_timer_when_no_expiry(client, make_key, monkeypatch):
    """Pastes with no expiry do not call schedule_paste_expiry."""
    import pste_server.main as main_mod
    scheduled = []
    monkeypatch.setattr(main_mod, "schedule_paste_expiry", lambda pid, exp: scheduled.append(pid))

    key = make_key()
    r = client.post("/", data={"pste": "hello"}, headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 200
    assert len(scheduled) == 0


def test_rate_limit_returns_429(client, monkeypatch):
    import pste_server.ratelimit as rl
    monkeypatch.setattr(rl, "is_allowed", lambda req: False)
    r = client.get("/")
    assert r.status_code == 429


# ---------------------------------------------------------------------------
# models: get_engine postgresql path and get_session
# ---------------------------------------------------------------------------

def test_get_engine_postgresql_path(monkeypatch):
    import pste_server.models as models_mod
    from unittest.mock import MagicMock, patch

    fake_engine = MagicMock()
    monkeypatch.setenv("STORAGE_BACKEND", "postgresql")
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost/test")
    models_mod._engine = None

    with patch("pste_server.models.create_engine", return_value=fake_engine) as mock_create, \
         patch("pste_server.models.Base.metadata.create_all"):
        engine = models_mod.get_engine()

    assert engine is fake_engine
    mock_create.assert_called_once()
    call_args = mock_create.call_args
    assert "postgresql://test:test@localhost/test" in call_args[0][0]


def test_get_session_yields_db(db_engine):
    """get_session() is a generator that yields a Session and closes it on exit."""
    from pste_server.models import get_session
    gen = get_session()
    db = next(gen)
    assert db is not None
    try:
        next(gen)
    except StopIteration:
        pass


# ---------------------------------------------------------------------------
# storage: SqlStorage.delete is a no-op
# ---------------------------------------------------------------------------

def test_sql_storage_delete_is_noop():
    from pste_server.storage import SqlStorage
    s = SqlStorage()
    s.delete("TESTID", None)  # Should not raise


# ---------------------------------------------------------------------------
# Validation edge cases (uncovered paths)
# ---------------------------------------------------------------------------

def test_validate_lang_none_reserved():
    from pste_server.validation import validate_lang
    with pytest.raises(ValueError, match="reserved"):
        validate_lang("none")


def test_validate_expires_in_overflow():
    from pste_server.validation import validate_expires_in
    with pytest.raises(ValueError, match="too large"):
        validate_expires_in("99999999999999", "H")


def test_validate_expires_at_no_timezone():
    from pste_server.validation import validate_expires_at
    with pytest.raises(ValueError, match="timezone"):
        validate_expires_at("2099-01-01T00:00:00")


def test_validate_expires_at_in_past():
    from pste_server.validation import validate_expires_at
    with pytest.raises(ValueError, match="future"):
        validate_expires_at("2000-01-01T00:00:00Z")


# ---------------------------------------------------------------------------
# main.py: missing pste field, auto-detect high confidence, run()
# ---------------------------------------------------------------------------

def test_post_missing_pste_field_422(client, make_key):
    key = make_key()
    r = client.post("/", data={}, headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 422
    assert "pste" in r.text.lower()


def test_post_auto_detect_high_confidence(client, make_key, monkeypatch):
    """Auto-detect returns a ?lang URL when Pygments is confident (score > 0.5)."""
    import pste_server.main as main_mod

    class _FakeLexer:
        aliases = ["python"]

        @staticmethod
        def analyse_text(text):
            return 0.9

    monkeypatch.setattr(main_mod, "guess_lexer", lambda content: _FakeLexer())

    key = make_key()
    r = client.post(
        "/",
        data={"pste": "def hello():\n    return 'world'\n", "auto_detect": "1"},
        headers={"Authorization": f"Bearer {key}"},
    )
    assert r.status_code == 200
    assert "?python" in r.text


def test_run_invokes_uvicorn(monkeypatch):
    from unittest.mock import patch
    with patch("uvicorn.run") as mock_run:
        from pste_server.main import run
        run()
    mock_run.assert_called_once()


# ---------------------------------------------------------------------------
# id_gen: bump_id_length_if_needed
# ---------------------------------------------------------------------------

def test_get_engine_unknown_backend_raises(monkeypatch):
    import pste_server.models as models_mod
    monkeypatch.setenv("STORAGE_BACKEND", "unknown_backend")
    models_mod._engine = None
    with pytest.raises(ValueError, match="Unknown"):
        models_mod.get_engine()


def test_validate_content_utf8_surrogate_raises():
    from pste_server.validation import validate_content
    with pytest.raises(ValueError, match="UTF-8"):
        validate_content("\ud800")  # lone surrogate, can't encode to UTF-8


def test_validate_expires_at_invalid_format():
    from pste_server.validation import validate_expires_at
    with pytest.raises(ValueError, match="ISO8601"):
        validate_expires_at("not-a-date")


def test_ratelimit_bucket_popleft():
    """Sliding window prunes old entries from the bucket."""
    from collections import deque
    from collections import defaultdict
    from pste_server.ratelimit import _check, WINDOW_SECONDS

    buckets = defaultdict(deque)
    ip = "1.2.3.4"
    old_time = 0.0  # ancient timestamp, well outside the window
    buckets[ip].append(old_time)

    now = WINDOW_SECONDS + 10.0  # old entry should be pruned
    result = _check(buckets, ip, now)
    assert result is True
    assert old_time not in buckets[ip]  # popleft was called


def test_bump_id_length_when_threshold_reached(db_engine):
    from pste_server.id_gen import bump_id_length_if_needed, get_id_length
    from sqlalchemy.orm import sessionmaker

    Session = sessionmaker(bind=db_engine)
    db = Session()

    current_length = get_id_length(db)
    threshold = int(36 ** current_length * 0.01)
    bump_id_length_if_needed(threshold, db)

    import pste_server.id_gen as id_gen_mod
    id_gen_mod._id_length_cache = None
    new_length = get_id_length(db)
    db.close()

    assert new_length == current_length + 1


def test_post_auto_detect_exception_returns_plain_url(client, make_key, monkeypatch):
    """If guess_lexer raises, auto-detect exception is swallowed and plain URL returned."""
    import pste_server.main as main_mod

    def _raising(content):
        raise RuntimeError("Pygments unavailable")

    monkeypatch.setattr(main_mod, "guess_lexer", _raising)

    key = make_key()
    r = client.post("/", data={"pste": "hello", "auto_detect": "1"},
                    headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 200
    # No ?lang since detection failed
    assert "?" not in r.text.split("/")[-1].strip()


def test_post_id_collision_retries(client, make_key, db_session, monkeypatch):
    """When generate_id returns a colliding ID, create_paste retries with a new one."""
    import pste_server.main as main_mod
    from pste_server.models import Key, Paste

    key = make_key()

    # Pre-create a paste with a known ID to force a collision
    existing = Paste(id="ZZZZZZ", created_by=key, content="existing", size_bytes=8)
    db_session.add(existing)
    db_session.commit()

    call_count = [0]
    original_gen = main_mod.generate_id

    def _mock_gen(length):
        call_count[0] += 1
        if call_count[0] == 1:
            return "ZZZZZZ"  # collision
        return original_gen(length)

    monkeypatch.setattr(main_mod, "generate_id", _mock_gen)

    r = client.post("/", data={"pste": "hello"}, headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 200
    assert call_count[0] >= 2  # Had to retry


def test_bump_id_length_creates_row_when_missing(db_engine):
    """bump_id_length_if_needed inserts a new ServerState row when none exists.

    The cache is left intact after deleting the row so that get_id_length() inside
    bump skips the DB query; the row-level bump query then finds nothing and hits
    the else-branch (db.add).
    """
    from pste_server.id_gen import bump_id_length_if_needed, get_id_length
    from pste_server.models import ServerState
    from sqlalchemy.orm import sessionmaker
    import pste_server.id_gen as id_gen_mod

    Session = sessionmaker(bind=db_engine)
    db = Session()
    length = get_id_length(db)  # Populates cache

    # Delete the row while keeping the cache — get_id_length won't re-query
    db.query(ServerState).filter_by(key="id_length").delete()
    db.commit()

    threshold = int(36 ** length * 0.01)
    bump_id_length_if_needed(threshold, db)  # Should insert a new row

    id_gen_mod._id_length_cache = None
    new_length = get_id_length(db)
    db.close()

    assert new_length == length + 1
