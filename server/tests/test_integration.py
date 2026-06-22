"""
Integration tests: real uvicorn server + real pste CLI subprocess.
Skipped if the pste CLI is not installed in PATH.
"""
import shutil
import socket
import subprocess
import sys
import time

import pytest
import requests as _requests

pste_bin = shutil.which("pste")
pytestmark = pytest.mark.skipif(pste_bin is None, reason="pste CLI not in PATH")


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def live_server(tmp_path_factory):
    """Start a real uvicorn server for the duration of the integration test module."""
    import os

    tmp = tmp_path_factory.mktemp("integration")
    db_path = str(tmp / "pste.db")
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"

    env = os.environ.copy()
    env.update({
        "STORAGE_BACKEND": "sqlite",
        "SQLITE_PATH": db_path,
        "BASE_URL": base_url,
        "PORT": str(port),
    })

    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "pste_server.main:app",
         "--host", "127.0.0.1", "--port", str(port)],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            _requests.get(base_url + "/", timeout=1)
            break
        except Exception:
            time.sleep(0.2)
    else:
        proc.terminate()
        pytest.fail("Integration server did not start in time")

    yield {"base_url": base_url, "env": env, "db_path": db_path}

    proc.terminate()
    proc.wait(timeout=5)


@pytest.fixture
def api_key(live_server):
    """Create a fresh API key via pste-admin and return the key value."""
    from urllib.parse import parse_qs, urlparse
    result = subprocess.run(
        ["pste-admin", "key", "add", "--user", "integration-test"],
        env=live_server["env"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    url = result.stdout.strip()
    return parse_qs(urlparse(url).query)["key"][0]


@pytest.fixture
def pste_env(live_server, api_key):
    """Return env dict for pste CLI invocations with PSTE_URL configured."""
    import os
    env = os.environ.copy()
    env["PSTE_URL"] = f"{live_server['base_url']}/?key={api_key}"
    return env


def _pste(args, input_text=None, env=None):
    return subprocess.run(
        ["pste"] + args,
        input=input_text,
        capture_output=True,
        text=True,
        env=env,
    )


# ---------------------------------------------------------------------------
# Server basics
# ---------------------------------------------------------------------------

def test_server_responds(live_server):
    r = _requests.get(live_server["base_url"] + "/")
    assert r.status_code == 200
    assert "pste(1)" in r.text


def test_server_help_page_has_form_with_key(live_server, api_key):
    r = _requests.get(f"{live_server['base_url']}/?key={api_key}")
    assert r.status_code == 200
    assert 'name="pste_key"' in r.text
    assert 'name="lang"' in r.text
    assert 'name="expires_in_n"' in r.text


# ---------------------------------------------------------------------------
# CLI round-trips
# ---------------------------------------------------------------------------

def test_full_round_trip(pste_env, live_server):
    result = _pste([], input_text="hello integration\n", env=pste_env)
    assert result.returncode == 0, result.stderr
    url = result.stdout.strip()
    assert url.startswith(live_server["base_url"])

    paste_id = url.split("/")[-1].split("?")[0]
    result2 = _pste([paste_id], env=pste_env)
    assert result2.returncode == 0
    assert "hello integration" in result2.stdout


def test_fetch_by_full_url(pste_env, live_server):
    result = _pste([], input_text="fetch by url\n", env=pste_env)
    url = result.stdout.strip()
    result2 = _pste([url], env=pste_env)
    assert result2.returncode == 0
    assert "fetch by url" in result2.stdout


def test_fetch_full_url_with_lang_returns_plain_text(pste_env, live_server):
    """-l python returns a ?python URL; fetching that URL strips ? and gets plain text."""
    result = _pste(["-l", "python"], input_text="print('hello')\n", env=pste_env)
    assert result.returncode == 0
    url_with_lang = result.stdout.strip()
    assert "?python" in url_with_lang

    fetch = _pste([url_with_lang], env=pste_env)
    assert fetch.returncode == 0
    assert "print" in fetch.stdout
    assert "<html" not in fetch.stdout  # plain text, not highlighted HTML


def test_single_view_second_fetch_fails(pste_env, live_server):
    result = _pste(["-s"], input_text="one time only\n", env=pste_env)
    assert result.returncode == 0
    paste_id = result.stdout.strip().split("/")[-1].split("?")[0]

    r1 = _pste([paste_id], env=pste_env)
    assert r1.returncode == 0
    assert "one time only" in r1.stdout

    r2 = _pste([paste_id], env=pste_env)
    assert r2.returncode != 0
    assert "not found" in r2.stderr.lower()


# ---------------------------------------------------------------------------
# Lang flag behaviour
# ---------------------------------------------------------------------------

def test_explicit_lang_flag_returns_highlighted_url(pste_env, live_server):
    """-l python → URL ends in ?python; fetching the bare ID returns plain text."""
    result = _pste(["-l", "python"], input_text="print('hi')\n", env=pste_env)
    assert result.returncode == 0
    url = result.stdout.strip()
    assert url.endswith("?python")

    paste_id = url.split("/")[-1].split("?")[0]
    fetch = _pste([paste_id], env=pste_env)
    assert fetch.returncode == 0
    assert "print" in fetch.stdout
    assert "<html" not in fetch.stdout


def test_auto_detect_lang_flag(pste_env, live_server):
    """-l (bare) requests auto-detection; Python code should produce a ?lang URL."""
    result = _pste(["-l"], input_text="import os\ndef main():\n    pass\n", env=pste_env)
    assert result.returncode == 0
    url = result.stdout.strip()
    # Python is detectable — expect a ?lang suffix
    assert "?" in url


def test_no_lang_flag_returns_plain_url(pste_env, live_server):
    """No -l flag → plain URL even for Python code."""
    result = _pste([], input_text="import os\ndef main():\n    pass\n", env=pste_env)
    assert result.returncode == 0
    url = result.stdout.strip()
    assert "?" not in url


# ---------------------------------------------------------------------------
# Expiry
# ---------------------------------------------------------------------------

def test_expire_flag_sets_expires_at(pste_env, live_server, api_key):
    result = _pste(["-e", "1D"], input_text="expires soon\n", env=pste_env)
    assert result.returncode == 0
    paste_id = result.stdout.strip().split("/")[-1].split("?")[0]

    admin_result = subprocess.run(
        ["pste-admin", "paste", "list"],
        env=live_server["env"],
        capture_output=True,
        text=True,
    )
    assert paste_id in admin_result.stdout


# ---------------------------------------------------------------------------
# Key management
# ---------------------------------------------------------------------------

def test_disabled_key_rejected(live_server, pste_env, api_key):
    subprocess.run(
        ["pste-admin", "key", "revoke", "--key", api_key],
        env=live_server["env"],
        capture_output=True,
    )
    result = _pste([], input_text="should fail\n", env=pste_env)
    assert result.returncode != 0
    assert "disabled" in result.stderr.lower() or "auth" in result.stderr.lower()


def test_key_add_output_is_bookmark_url(live_server):
    """pste-admin key add prints a full bookmark URL, not a bare key."""
    result = subprocess.run(
        ["pste-admin", "key", "add", "--user", "url-test"],
        env=live_server["env"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    url = result.stdout.strip()
    assert url.startswith(live_server["base_url"])
    assert "/?key=" in url
