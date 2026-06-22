import secrets

import pytest
from click.testing import CliRunner

from pste_server.admin import cli
from pste_server.models import Key, Paste


@pytest.fixture
def runner(db_engine):
    return CliRunner()


def _add_key(db_session, user="alice", notes=None, disabled=False):
    k = Key(key=secrets.token_hex(16), user=user, notes=notes, disabled=disabled)
    db_session.add(k)
    db_session.commit()
    return k.key


def _add_paste(db_session, key_val, paste_id="ABCDEF", lang=None):
    p = Paste(id=paste_id, created_by=key_val, content="hello", size_bytes=5, lang=lang)
    db_session.add(p)
    db_session.commit()
    return paste_id


# ---------------------------------------------------------------------------
# key add
# ---------------------------------------------------------------------------

def _extract_key(url: str) -> str:
    """Parse key from bookmark URL output: http://host/?key=VALUE"""
    from urllib.parse import parse_qs, urlparse
    return parse_qs(urlparse(url).query)["key"][0]


def test_key_add_prints_url(runner, db_session):
    result = runner.invoke(cli, ["key", "add"])
    assert result.exit_code == 0
    url = result.output.strip()
    assert "/?key=" in url
    key_val = _extract_key(url)
    assert key_val.isalnum()
    assert db_session.query(Key).filter(Key.key == key_val).first() is not None


def test_key_add_with_user_and_notes(runner, db_session):
    result = runner.invoke(cli, ["key", "add", "--user", "alice", "--notes", "test note"])
    assert result.exit_code == 0
    key_val = _extract_key(result.output.strip())
    k = db_session.query(Key).filter(Key.key == key_val).first()
    assert k.user == "alice"
    assert k.notes == "test note"
    assert k.disabled is False


def test_key_add_custom_key(runner, db_session):
    result = runner.invoke(cli, ["key", "add", "--key", "MyCustomKey123"])
    assert result.exit_code == 0
    assert "MyCustomKey123" in result.output
    assert db_session.query(Key).filter(Key.key == "MyCustomKey123").first() is not None


def test_key_add_custom_key_invalid_chars(runner, db_session):
    result = runner.invoke(cli, ["key", "add", "--key", "bad-key!"])
    assert result.exit_code != 0
    assert "A-Za-z0-9" in result.output


def test_key_add_duplicate_custom_key(runner, db_session):
    runner.invoke(cli, ["key", "add", "--key", "SameKey"])
    result = runner.invoke(cli, ["key", "add", "--key", "SameKey"])
    assert result.exit_code != 0
    assert "already exists" in result.output


# ---------------------------------------------------------------------------
# key list
# ---------------------------------------------------------------------------

def test_key_list_shows_keys(runner, db_session):
    _add_key(db_session, user="alice")
    _add_key(db_session, user="bob")
    result = runner.invoke(cli, ["key", "list"])
    assert result.exit_code == 0
    assert "alice" in result.output
    assert "bob" in result.output


def test_key_list_empty(runner, db_session):
    result = runner.invoke(cli, ["key", "list"])
    assert result.exit_code == 0
    assert "No keys" in result.output


# ---------------------------------------------------------------------------
# key set
# ---------------------------------------------------------------------------

def test_key_set_disabled_by_key(runner, db_session):
    key_val = _add_key(db_session)
    result = runner.invoke(cli, ["key", "set", "--key", key_val, "--disabled", "true"])
    assert result.exit_code == 0
    db_session.expire_all()
    assert db_session.query(Key).filter(Key.key == key_val).first().disabled is True


def test_key_set_notes_by_user(runner, db_session):
    _add_key(db_session, user="alice")
    _add_key(db_session, user="alice")
    result = runner.invoke(cli, ["key", "set", "--user", "alice", "--notes", "updated"])
    assert result.exit_code == 0
    assert "2 key(s)" in result.output
    db_session.expire_all()
    assert all(k.notes == "updated" for k in db_session.query(Key).filter(Key.user == "alice").all())


def test_key_set_no_matcher_fails(runner, db_session):
    result = runner.invoke(cli, ["key", "set", "--notes", "x"])
    assert result.exit_code != 0


def test_key_set_no_match_fails(runner, db_session):
    result = runner.invoke(cli, ["key", "set", "--key", "doesnotexist", "--disabled", "true"])
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# key revoke
# ---------------------------------------------------------------------------

def test_key_revoke_by_key(runner, db_session):
    key_val = _add_key(db_session)
    result = runner.invoke(cli, ["key", "revoke", "--key", key_val])
    assert result.exit_code == 0
    db_session.expire_all()
    assert db_session.query(Key).filter(Key.key == key_val).first().disabled is True


def test_key_revoke_by_user_confirmed(runner, db_session):
    _add_key(db_session, user="alice")
    _add_key(db_session, user="alice")
    result = runner.invoke(cli, ["key", "revoke", "--user", "alice"], input="y\n")
    assert result.exit_code == 0
    assert "Revoked 2 key(s)" in result.output
    db_session.expire_all()
    assert all(k.disabled for k in db_session.query(Key).filter(Key.user == "alice").all())


def test_key_revoke_by_user_cancelled(runner, db_session):
    _add_key(db_session, user="alice")
    result = runner.invoke(cli, ["key", "revoke", "--user", "alice"], input="n\n")
    assert result.exit_code == 0
    assert "Cancelled" in result.output
    db_session.expire_all()
    assert not db_session.query(Key).filter(Key.user == "alice").first().disabled


def test_key_revoke_by_notes_confirmed(runner, db_session):
    _add_key(db_session, user="alice", notes="laptop")
    _add_key(db_session, user="bob", notes="laptop")
    result = runner.invoke(cli, ["key", "revoke", "--notes", "laptop"], input="y\n")
    assert result.exit_code == 0
    assert "Revoked 2 key(s)" in result.output


def test_key_revoke_no_args_fails(runner, db_session):
    result = runner.invoke(cli, ["key", "revoke"])
    assert result.exit_code != 0


def test_key_revoke_not_found(runner, db_session):
    result = runner.invoke(cli, ["key", "revoke", "--key", "doesnotexist"])
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# paste list
# ---------------------------------------------------------------------------

def test_paste_list_shows_pastes(runner, db_session):
    key_val = _add_key(db_session, user="alice")
    _add_paste(db_session, key_val, "AAAAAA")
    result = runner.invoke(cli, ["paste", "list"])
    assert result.exit_code == 0
    assert "AAAAAA" in result.output


def test_paste_list_shows_lang(runner, db_session):
    key_val = _add_key(db_session, user="alice")
    _add_paste(db_session, key_val, "AAAAAA", lang="python")
    result = runner.invoke(cli, ["paste", "list"])
    assert result.exit_code == 0
    assert "python" in result.output


def test_paste_list_filter_by_user(runner, db_session):
    alice_key = _add_key(db_session, user="alice")
    bob_key = _add_key(db_session, user="bob")
    _add_paste(db_session, alice_key, "AAAAAA")
    _add_paste(db_session, bob_key, "BBBBBB")
    result = runner.invoke(cli, ["paste", "list", "--user", "alice"])
    assert result.exit_code == 0
    assert "AAAAAA" in result.output
    assert "BBBBBB" not in result.output


def test_paste_list_empty(runner, db_session):
    result = runner.invoke(cli, ["paste", "list"])
    assert result.exit_code == 0
    assert "No pastes" in result.output


def test_key_set_user_field(runner, db_session):
    """key set --set-user changes the user field on matching keys."""
    key_val = _add_key(db_session, user="old-user")
    result = runner.invoke(cli, ["key", "set", "--key", key_val, "--set-user", "new-user"])
    assert result.exit_code == 0
    from pste_server.models import Key
    k = db_session.query(Key).filter_by(key=key_val).first()
    db_session.expire(k)
    k = db_session.query(Key).filter_by(key=key_val).first()
    assert k.user == "new-user"


def test_paste_list_filter_by_key(runner, db_session):
    """paste list --key shows only pastes by that key."""
    key1 = _add_key(db_session, user="alice")
    key2 = _add_key(db_session, user="bob")
    _add_paste(db_session, key1, "AAAAAA")
    _add_paste(db_session, key2, "BBBBBB")
    result = runner.invoke(cli, ["paste", "list", "--key", key1])
    assert result.exit_code == 0
    assert "AAAAAA" in result.output
    assert "BBBBBB" not in result.output
