import argparse
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import requests

TTL_RE = re.compile(r"^(\d+)([MmWwDdHh])$")
_UNIT_SECONDS = {
    "H": 3600,
    "D": 86400,
    "W": 7 * 86400,
    "M": 60,
}

_TIMEOUT = 10
_URL_ERROR = (
    "error: PSTE_URL not set — set it to your pste bookmark URL, "
    "e.g. https://pste.example.com/?key=your-key"
)


def _parse_pste_url():
    """Return (server, api_key) from PSTE_URL, or (None, None) if unset."""
    raw = os.environ.get("PSTE_URL", "").strip()
    if not raw:
        return None, None
    parsed = urlparse(raw)
    key = parse_qs(parsed.query).get("key", [None])[0]
    server = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
    return server, key


def _parse_ttl(value: str) -> str:
    """Parse TTL string like '7d', '2W', '48H'; return ISO8601 UTC expires_at."""
    m = TTL_RE.match(value)
    if not m:
        print(f"error: invalid expire format {value!r} (expected e.g. 7d, 2W, 48H, 3M)", file=sys.stderr)
        sys.exit(1)
    n, unit = int(m.group(1)), m.group(2).upper()
    dt = datetime.now(timezone.utc) + timedelta(seconds=n * _UNIT_SECONDS[unit])
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _validate_lang(lang: str) -> str:
    from pygments.lexers import get_lexer_by_name
    from pygments.util import ClassNotFound
    try:
        get_lexer_by_name(lang)
    except ClassNotFound:
        print(f"error: unknown language/lexer {lang!r}", file=sys.stderr)
        sys.exit(1)
    return lang


def _fetch_paste(url: str) -> int:
    try:
        resp = requests.get(url, timeout=_TIMEOUT)
    except requests.exceptions.ConnectTimeout:
        print("error: http connection timed out", file=sys.stderr)
        return 1
    except requests.exceptions.ReadTimeout:
        print("error: http request timed out", file=sys.stderr)
        return 1

    if resp.status_code == 404:
        print(f"error: paste not found", file=sys.stderr)
        return 1
    if resp.status_code != 200:
        print(f"error: server returned HTTP {resp.status_code}", file=sys.stderr)
        return 1

    print(resp.text.rstrip())
    return 0


def _create_paste(content: str, args, server: str, api_key: str) -> int:
    data = {"pste": content}
    if args.single_view:
        data["single_view"] = "1"
    if args.expire:
        data["expires_at"] = _parse_ttl(args.expire)
    if args.lang is None:
        pass  # no -l flag: no lang, no auto-detection
    elif args.lang == "":
        data["auto_detect"] = "1"  # -l with no value: request auto-detection
    else:
        data["lang"] = _validate_lang(args.lang)  # -l python: explicit lang

    try:
        resp = requests.post(
            server + "/",
            data=data,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=_TIMEOUT,
        )
    except requests.exceptions.ConnectTimeout:
        print("error: http connection timed out", file=sys.stderr)
        return 1
    except requests.exceptions.ReadTimeout:
        print("error: http request timed out", file=sys.stderr)
        return 1

    if resp.status_code == 401:
        print("error: authentication failed — check your API key", file=sys.stderr)
        return 1
    if resp.status_code == 403:
        print("error: API key is disabled", file=sys.stderr)
        return 1
    if resp.status_code != 200:
        print(f"error: server returned HTTP {resp.status_code}: {resp.text.strip()}", file=sys.stderr)
        return 1

    print(resp.text.strip())
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Upload text from stdin to a pste server. "
            "If [id] is provided, the paste is fetched instead."
        ),
        epilog="environment variables:\n  PSTE_URL  server URL with API key, e.g. https://pste.example.com/?key=your-key",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("id", nargs="?", help="Fetch paste by ID or full URL")
    parser.add_argument("-s", "--single-view", action="store_true", help="Single-view paste")
    parser.add_argument("-e", "--expire", metavar="TTL", help="Expiry: e.g. 7D, 2W, 48H, 30M (M=minutes)")
    parser.add_argument(
        "-l", "--lang", "--language",
        metavar="LEXER", nargs="?", const="", default=None,
        help="Syntax highlighting language (omit value to auto-detect)",
    )
    args = parser.parse_args()

    if args.id is not None:
        # Fetch mode
        if args.single_view or args.expire or args.lang is not None:
            print("error: -s/-e/-l flags are not valid when fetching a paste", file=sys.stderr)
            return 1

        paste_id = args.id
        if paste_id.startswith("http"):
            # Strip any ?lang query string — bare GET always returns plain text
            parsed = urlparse(paste_id)
            clean_url = parsed._replace(query="").geturl()
            return _fetch_paste(clean_url)

        # Bare ID — need server from PSTE_URL
        server, _ = _parse_pste_url()
        if not server:
            print(_URL_ERROR, file=sys.stderr)
            return 1
        return _fetch_paste(f"{server}/{paste_id}")

    # Create mode — read from stdin
    server, api_key = _parse_pste_url()
    if not server or not api_key:
        print(_URL_ERROR, file=sys.stderr)
        return 1

    try:
        content = sys.stdin.read()
    except UnicodeDecodeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    if not content:
        print("error: empty input", file=sys.stderr)
        return 1

    return _create_paste(content, args, server, api_key)


if __name__ == "__main__":
    sys.exit(main())
