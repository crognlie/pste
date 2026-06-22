import io
import sys

import pytest
import responses as rsps_lib

from pste.cli import _parse_ttl, _validate_lang, main


SERVER = "http://pste.example.com"
PSTE_URL = f"{SERVER}/?key=testkey"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("PSTE_URL", PSTE_URL)


# ---------------------------------------------------------------------------
# TTL parsing
# ---------------------------------------------------------------------------

def test_parse_ttl_valid_formats():
    for fmt in ("7d", "7D", "2W", "2w", "48H", "48h", "3M", "3m"):
        result = _parse_ttl(fmt)
        assert result.endswith("Z")
        assert "T" in result


def test_parse_ttl_invalid_exits():
    with pytest.raises(SystemExit):
        _parse_ttl("badformat")


def test_parse_ttl_invalid_fraction_exits():
    with pytest.raises(SystemExit):
        _parse_ttl("1.5d")


# ---------------------------------------------------------------------------
# Lang validation
# ---------------------------------------------------------------------------

def test_validate_lang_known():
    assert _validate_lang("python") == "python"


def test_validate_lang_unknown_exits():
    with pytest.raises(SystemExit):
        _validate_lang("notareallexer12345")


# ---------------------------------------------------------------------------
# Fetch mode
# ---------------------------------------------------------------------------

@rsps_lib.activate
def test_fetch_by_id_success(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["pste", "ABC123"])
    rsps_lib.add(rsps_lib.GET, f"{SERVER}/ABC123", body="hello world\n", status=200)
    rc = main()
    assert rc == 0
    assert "hello world" in capsys.readouterr().out


@rsps_lib.activate
def test_fetch_404(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["pste", "ZZZZZZ"])
    rsps_lib.add(rsps_lib.GET, f"{SERVER}/ZZZZZZ", status=404)
    rc = main()
    assert rc == 1
    assert "not found" in capsys.readouterr().err


@rsps_lib.activate
def test_fetch_server_error(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["pste", "ABC123"])
    rsps_lib.add(rsps_lib.GET, f"{SERVER}/ABC123", status=500)
    rc = main()
    assert rc == 1
    assert "500" in capsys.readouterr().err


def test_fetch_timeout(monkeypatch, capsys):
    import requests.exceptions
    monkeypatch.setattr(sys, "argv", ["pste", "ABC123"])
    with rsps_lib.RequestsMock() as r:
        r.add(rsps_lib.GET, f"{SERVER}/ABC123", body=requests.exceptions.ConnectTimeout())
        rc = main()
    assert rc == 1
    assert "timed out" in capsys.readouterr().err


@rsps_lib.activate
def test_fetch_from_full_url_no_pste_url_needed(monkeypatch, capsys):
    """Full URL can be fetched without PSTE_URL set."""
    monkeypatch.delenv("PSTE_URL", raising=False)
    monkeypatch.setattr(sys, "argv", ["pste", f"{SERVER}/ABC123"])
    rsps_lib.add(rsps_lib.GET, f"{SERVER}/ABC123", body="content\n", status=200)
    rc = main()
    assert rc == 0
    assert "content" in capsys.readouterr().out


@rsps_lib.activate
def test_fetch_full_url_with_lang_query_stripped(monkeypatch, capsys):
    """Full URL ending in ?python is stripped before the GET request."""
    monkeypatch.setattr(sys, "argv", ["pste", f"{SERVER}/ABC123?python"])
    rsps_lib.add(rsps_lib.GET, f"{SERVER}/ABC123", body="plain content\n", status=200)
    rc = main()
    assert rc == 0
    assert "plain content" in capsys.readouterr().out
    # Confirm the request was made without the query string
    assert rsps_lib.calls[0].request.url == f"{SERVER}/ABC123"


def test_fetch_by_id_no_pste_url(monkeypatch, capsys):
    monkeypatch.delenv("PSTE_URL", raising=False)
    monkeypatch.setattr(sys, "argv", ["pste", "ABC123"])
    rc = main()
    assert rc == 1
    assert "PSTE_URL" in capsys.readouterr().err


def test_fetch_flags_rejected_in_fetch_mode(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["pste", "-s", "ABC123"])
    rc = main()
    assert rc == 1
    assert "not valid" in capsys.readouterr().err


def test_fetch_bare_l_flag_rejected_in_fetch_mode(monkeypatch, capsys):
    """-l with no value is still a flag and must be rejected in fetch mode."""
    monkeypatch.setattr(sys, "argv", ["pste", "-l", "ABC123"])
    # -l consumes ABC123 as the lang value, leaving no positional ID → create mode
    # but stdin is empty → error
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    rc = main()
    assert rc == 1


# ---------------------------------------------------------------------------
# Create mode
# ---------------------------------------------------------------------------

@rsps_lib.activate
def test_create_success(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["pste"])
    monkeypatch.setattr(sys, "stdin", io.StringIO("hello world"))
    rsps_lib.add(rsps_lib.POST, f"{SERVER}/", body=f"{SERVER}/AB1234\n", status=200)
    rc = main()
    assert rc == 0
    assert "AB1234" in capsys.readouterr().out


@rsps_lib.activate
def test_create_sends_auth_header(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["pste"])
    monkeypatch.setattr(sys, "stdin", io.StringIO("hello"))
    rsps_lib.add(rsps_lib.POST, f"{SERVER}/", body=f"{SERVER}/AB1234\n", status=200)
    main()
    assert rsps_lib.calls[0].request.headers["Authorization"] == "Bearer testkey"


def test_create_no_pste_url(monkeypatch, capsys):
    monkeypatch.delenv("PSTE_URL", raising=False)
    monkeypatch.setattr(sys, "argv", ["pste"])
    monkeypatch.setattr(sys, "stdin", io.StringIO("hello"))
    rc = main()
    assert rc == 1
    assert "PSTE_URL" in capsys.readouterr().err


@rsps_lib.activate
def test_create_auth_failure(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["pste"])
    monkeypatch.setattr(sys, "stdin", io.StringIO("hello"))
    rsps_lib.add(rsps_lib.POST, f"{SERVER}/", status=401)
    rc = main()
    assert rc == 1
    assert "authentication" in capsys.readouterr().err


@rsps_lib.activate
def test_create_disabled_key(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["pste"])
    monkeypatch.setattr(sys, "stdin", io.StringIO("hello"))
    rsps_lib.add(rsps_lib.POST, f"{SERVER}/", status=403)
    rc = main()
    assert rc == 1
    assert "disabled" in capsys.readouterr().err


@rsps_lib.activate
def test_create_server_error(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["pste"])
    monkeypatch.setattr(sys, "stdin", io.StringIO("hello"))
    rsps_lib.add(rsps_lib.POST, f"{SERVER}/", status=500, body="oops")
    rc = main()
    assert rc == 1
    assert "500" in capsys.readouterr().err


def test_create_empty_stdin(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["pste"])
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    rc = main()
    assert rc == 1
    assert "empty" in capsys.readouterr().err


@rsps_lib.activate
def test_create_single_view_flag(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["pste", "-s"])
    monkeypatch.setattr(sys, "stdin", io.StringIO("secret"))
    rsps_lib.add(rsps_lib.POST, f"{SERVER}/", body=f"{SERVER}/AB1234\n", status=200)
    main()
    assert "single_view=1" in rsps_lib.calls[0].request.body


@rsps_lib.activate
def test_create_explicit_lang_flag(monkeypatch, capsys):
    """-l python sends lang=python and prints the ?python URL."""
    monkeypatch.setattr(sys, "argv", ["pste", "-l", "python"])
    monkeypatch.setattr(sys, "stdin", io.StringIO("print('hi')"))
    rsps_lib.add(rsps_lib.POST, f"{SERVER}/", body=f"{SERVER}/AB1234?python\n", status=200)
    rc = main()
    assert rc == 0
    body = rsps_lib.calls[0].request.body
    assert "lang=python" in body
    assert "auto_detect" not in body
    assert "AB1234?python" in capsys.readouterr().out


@rsps_lib.activate
def test_create_lang_auto_detect_bare_flag(monkeypatch, capsys):
    """-l with no value sends auto_detect=1 (not lang=); prints returned URL."""
    monkeypatch.setattr(sys, "argv", ["pste", "-l"])
    monkeypatch.setattr(sys, "stdin", io.StringIO("print('hi')"))
    rsps_lib.add(rsps_lib.POST, f"{SERVER}/", body=f"{SERVER}/AB1234?python\n", status=200)
    rc = main()
    assert rc == 0
    body = rsps_lib.calls[0].request.body
    assert "auto_detect=1" in body
    assert "lang=" not in body
    assert "AB1234?python" in capsys.readouterr().out


@rsps_lib.activate
def test_create_no_lang_flag_sends_neither(monkeypatch):
    """No -l flag: neither lang nor auto_detect is sent."""
    monkeypatch.setattr(sys, "argv", ["pste"])
    monkeypatch.setattr(sys, "stdin", io.StringIO("hello"))
    rsps_lib.add(rsps_lib.POST, f"{SERVER}/", body=f"{SERVER}/AB1234\n", status=200)
    main()
    body = rsps_lib.calls[0].request.body
    assert "lang=" not in body
    assert "auto_detect" not in body


@rsps_lib.activate
def test_create_expire_flag(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["pste", "-e", "7d"])
    monkeypatch.setattr(sys, "stdin", io.StringIO("hello"))
    rsps_lib.add(rsps_lib.POST, f"{SERVER}/", body=f"{SERVER}/AB1234\n", status=200)
    main()
    assert "expires_at=" in rsps_lib.calls[0].request.body


def test_fetch_read_timeout(monkeypatch, capsys):
    import responses as rsps_lib2
    import requests
    monkeypatch.setattr(sys, "argv", ["pste", "AB1234"])
    with rsps_lib2.RequestsMock() as rsps:
        rsps.add(rsps_lib2.GET, f"{SERVER}/AB1234", body=requests.exceptions.ReadTimeout())
        rc = main()
    assert rc == 1
    assert "timed out" in capsys.readouterr().err


def test_fetch_connect_timeout(monkeypatch, capsys):
    import responses as rsps_lib2
    import requests
    monkeypatch.setattr(sys, "argv", ["pste", "AB1234"])
    with rsps_lib2.RequestsMock() as rsps:
        rsps.add(rsps_lib2.GET, f"{SERVER}/AB1234", body=requests.exceptions.ConnectTimeout())
        rc = main()
    assert rc == 1
    assert "timed out" in capsys.readouterr().err


def test_create_read_timeout(monkeypatch, capsys):
    import responses as rsps_lib2
    import requests
    monkeypatch.setattr(sys, "argv", ["pste"])
    monkeypatch.setattr(sys, "stdin", io.StringIO("hello"))
    with rsps_lib2.RequestsMock() as rsps:
        rsps.add(rsps_lib2.POST, f"{SERVER}/", body=requests.exceptions.ReadTimeout())
        rc = main()
    assert rc == 1
    assert "timed out" in capsys.readouterr().err


def test_create_connect_timeout(monkeypatch, capsys):
    import responses as rsps_lib2
    import requests
    monkeypatch.setattr(sys, "argv", ["pste"])
    monkeypatch.setattr(sys, "stdin", io.StringIO("hello"))
    with rsps_lib2.RequestsMock() as rsps:
        rsps.add(rsps_lib2.POST, f"{SERVER}/", body=requests.exceptions.ConnectTimeout())
        rc = main()
    assert rc == 1
    assert "timed out" in capsys.readouterr().err


def test_create_unicode_decode_error(monkeypatch, capsys):
    """UnicodeDecodeError on stdin.read() returns exit code 2."""
    import io

    class _BadStdin:
        def read(self):
            raise UnicodeDecodeError("utf-8", b"", 0, 1, "invalid")

    monkeypatch.setattr(sys, "argv", ["pste"])
    monkeypatch.setattr(sys, "stdin", _BadStdin())
    rc = main()
    assert rc == 2
    assert "error" in capsys.readouterr().err
