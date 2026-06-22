import html as _html
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, Form, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name, guess_lexer
from pygments.util import ClassNotFound
from sqlalchemy import delete as sa_delete, update as sa_update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pste_server.config import hard_delete_on_single_view
from pste_server.id_gen import generate_id, get_id_length
from pste_server.models import Key, Paste, get_engine, get_session
from pste_server.reaper import schedule_paste_expiry, start_reaper, stop_reaper
from pste_server.storage import get_storage
from pste_server.validation import (
    ALLOWED_POST_FIELDS,
    validate_content,
    validate_expires_at,
    validate_expires_in,
    validate_lang,
)

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000").rstrip("/")

_DARK_MODE = os.environ.get("DARK_MODE", "").lower() in ("1", "true", "yes")
HIGHLIGHT_STYLE = os.environ.get("HIGHLIGHT_STYLE", "github-dark" if _DARK_MODE else "default")


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_engine()
    start_reaper()
    yield
    stop_reaper()


app = FastAPI(lifespan=lifespan)


@app.middleware("http")
async def add_source_header(request, call_next):
    response = await call_next(request)
    response.headers["X-Source"] = "https://github.com/crognlie/pste"
    return response


@app.middleware("http")
async def rate_limit(request: Request, call_next):
    from pste_server.ratelimit import is_allowed
    if not is_allowed(request):
        return Response("Too Many Requests\n", status_code=429, media_type="text/plain")
    return await call_next(request)


def _auth_key(authorization: str | None, db: Session) -> Key:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = authorization[len("Bearer "):]
    key = db.query(Key).filter(Key.key == token).first()
    if not key:
        raise HTTPException(status_code=401, detail="Invalid API key")
    if key.disabled:
        raise HTTPException(status_code=403, detail="API key is disabled")
    return key


def _render_highlighted(content: str, lang: str, headers: dict | None = None) -> Response:
    try:
        lexer = get_lexer_by_name(lang)
    except ClassNotFound:
        lexer = get_lexer_by_name("text")
    formatter = HtmlFormatter(style=HIGHLIGHT_STYLE, lineanchors="n", linenos="table")
    code_html = highlight(content, lexer, formatter)
    style_defs = formatter.get_style_defs(".highlight")
    full_html = (
        "<!DOCTYPE html><html><head><meta charset='UTF-8'><style>"
        f"{style_defs}"
        # Line numbers are in a separate column — suppress selection so
        # Ctrl-A copies only the paste content, not the line numbers.
        " td.linenos { user-select: none; -webkit-user-select: none; }"
        " body { margin: 1em; }"
        " #copy-btn { margin-bottom: 0.5em; cursor: pointer; }"
        "</style></head><body>"
        "<button id='copy-btn' onclick=\""
        "var b=document.getElementById('copy-btn');"
        "navigator.clipboard.writeText(document.querySelector('td.code pre').textContent)"
        ".then(function(){b.textContent='Copied!';setTimeout(function(){b.textContent='Copy';},2000);});"
        "\">Copy</button><br>"
        f"{code_html}"
        "</body></html>"
    )
    return Response(content=full_html, media_type="text/html; charset=UTF-8", headers=headers)


_LANG_OPTIONS = [
    ("", "auto-detect"),
    ("bash", "Bash"),
    ("c", "C"),
    ("cpp", "C++"),
    ("css", "CSS"),
    ("diff", "Diff"),
    ("docker", "Dockerfile"),
    ("go", "Go"),
    ("html", "HTML"),
    ("java", "Java"),
    ("javascript", "JavaScript"),
    ("json", "JSON"),
    ("kotlin", "Kotlin"),
    ("lua", "Lua"),
    ("make", "Makefile"),
    ("markdown", "Markdown"),
    ("nginx", "Nginx"),
    ("php", "PHP"),
    ("python", "Python"),
    ("ruby", "Ruby"),
    ("rust", "Rust"),
    ("scala", "Scala"),
    ("sql", "SQL"),
    ("swift", "Swift"),
    ("toml", "TOML"),
    ("typescript", "TypeScript"),
    ("xml", "XML"),
    ("yaml", "YAML"),
]


def _help_page(key: str | None = None) -> str:
    form_html = ""
    if key:
        lang_opts = "\n".join(
            f'  <option value="{v}">{_html.escape(label)}</option>'
            for v, label in _LANG_OPTIONS
        )
        form_html = f"""
<form action="/" method="POST" enctype="multipart/form-data">
  <input type="hidden" name="pste_key" value="{_html.escape(key)}">
  <input type="hidden" name="auto_detect" value="1">
  <textarea name="pste" cols="80" rows="24"></textarea><br>
  <label><input type="checkbox" name="single_view" value="1"> single-view</label>
  &nbsp; expires: <input type="number" name="expires_in_n" min="1" style="width:4em">
  <select name="expires_in_unit">
    <option value="">never</option>
    <option value="H">hours</option>
    <option value="D">days</option>
    <option value="W">weeks</option>
    <option value="M">minutes</option>
  </select>
  &nbsp; lang: <select name="lang">
{lang_opts}
  </select>
  &nbsp; <button type="submit">paste</button>
</form>"""

    return f"""<html><body><style>a{{text-decoration:none}}</style><pre>
pste(1)                             PSTE                             pste(1)

NAME
    pste: self-hosted command line paste server (sprunge-inspired).

SYNOPSIS
    &lt;command&gt; | curl -F 'pste=&lt;-' -H 'Authorization: Bearer KEY' {BASE_URL}
    pste &lt; file.txt
    echo hello | pste

DESCRIPTION
    GET  {BASE_URL}/&lt;id&gt;         fetch paste as plain text
    GET  {BASE_URL}/&lt;id&gt;?&lt;lang&gt;  fetch with syntax highlighting
    POST {BASE_URL}/             create paste (auth required)

EXAMPLES
    ~$ echo hello | pste
       {BASE_URL}/AB1234
    ~$ pste -l go &lt; main.go
       {BASE_URL}/AB1235?go
    ~$ pste -l &lt; data.json        # auto-detect language
       {BASE_URL}/AB1236?json
    ~$ pste AB1234
       hello
    ~$ echo hello | pste -s
       {BASE_URL}/AB1237        # only viewable once
    ~$ echo hello | pste -e 7d
       {BASE_URL}/AB1238        # expires after 7 days
       # expiry format: &lt;n&gt;H hours · &lt;n&gt;D days · &lt;n&gt;W weeks · &lt;n&gt;M minutes
       # e.g. 24H, 7D, 2W, 30M

SEE ALSO
    https://github.com/crognlie/pste
</pre>
{form_html}
</body></html>"""


def _result_page(primary_url: str, secondary_url: str | None = None, secondary_label: str | None = None) -> str:
    secondary = ""
    if secondary_url and secondary_label:
        secondary = f'\n<a href="{_html.escape(secondary_url)}">view highlighted ({_html.escape(secondary_label)})</a>'
    return (
        f"<html><body><style>a{{text-decoration:none}}</style><pre>"
        f'<a href="{_html.escape(primary_url)}">{_html.escape(primary_url)}</a>'
        f"{secondary}"
        f"</pre></body></html>"
    )


@app.get("/", response_class=HTMLResponse)
async def index(key: str | None = None):
    return _help_page(key=key)


@app.post("/")
async def create_paste(
    request: Request,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_session),
):
    form = await request.form()

    # Reject unknown fields. Web form sends pste_key as a proxy for auth.
    allowed = ALLOWED_POST_FIELDS | {"pste_key"}
    extra = set(form.keys()) - allowed
    if extra:
        raise HTTPException(status_code=422, detail=f"Unknown fields: {sorted(extra)}")

    # Web form uses hidden pste_key field instead of Authorization header
    web_key = form.get("pste_key")
    is_web_form = False
    if web_key and not authorization:
        authorization = f"Bearer {web_key}"
        is_web_form = True

    key_obj = _auth_key(authorization, db)

    raw = form.get("pste")
    if not raw:
        raise HTTPException(status_code=422, detail="Field 'pste' is required")

    try:
        content = validate_content(str(raw))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    auto_detect_requested = str(form.get("auto_detect", "")).lower() in ("1", "true")

    lang = None
    explicit_lang = False
    raw_lang = form.get("lang")
    if raw_lang:
        try:
            lang = validate_lang(str(raw_lang))
            explicit_lang = True
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

    expires_at = None
    raw_expires = form.get("expires_at")
    expires_in_n = str(form.get("expires_in_n", "")).strip()
    expires_in_unit = str(form.get("expires_in_unit", "")).strip()
    if raw_expires:
        try:
            expires_at = validate_expires_at(str(raw_expires))
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
    elif expires_in_n and expires_in_unit:
        try:
            expires_at = validate_expires_in(expires_in_n, expires_in_unit)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

    single_view_raw = form.get("single_view", "")
    single_view = str(single_view_raw).lower() in ("1", "true")

    # Auto-detect language only when explicitly requested (web form or -l flag with no value)
    auto_lang = None
    if not lang and auto_detect_requested:
        try:
            guessed = guess_lexer(content)
            score = type(guessed).analyse_text(content)
            if score > 0.5 and guessed.aliases:
                auto_lang = guessed.aliases[0]
                lang = auto_lang
        except Exception:
            pass

    storage = get_storage()
    id_length = get_id_length(db)

    for _ in range(10):
        paste_id = generate_id(id_length)
        if db.query(Paste).filter(Paste.id == paste_id).first():
            continue

        gcs_key = storage.store(paste_id, content)

        paste = Paste(
            id=paste_id,
            created_by=key_obj.key,
            expires_at=expires_at,
            single_view=single_view,
            lang=lang,
            size_bytes=len(content.encode("utf-8")),
            content=content if gcs_key is None else None,
            gcs_key=gcs_key,
        )
        db.add(paste)
        try:
            db.commit()
            if expires_at is not None:
                schedule_paste_expiry(paste_id, expires_at)
            break
        except IntegrityError:
            db.rollback()
            if gcs_key is not None:
                try:
                    storage.delete(paste_id, gcs_key)
                except Exception:
                    pass
    else:
        raise HTTPException(status_code=500, detail="Could not generate unique ID")

    plain_url = f"{BASE_URL}/{paste_id}"
    highlighted_url = f"{plain_url}?{lang}" if lang else None

    if is_web_form:
        if explicit_lang:
            # User picked a language — show the highlighted URL directly
            return HTMLResponse(_result_page(highlighted_url))
        elif auto_lang:
            # Auto-detected — show plain URL and offer highlighted as option
            return HTMLResponse(_result_page(plain_url, highlighted_url, auto_lang))
        else:
            # No lang — plain URL only
            return HTMLResponse(_result_page(plain_url))

    # API/CLI: return URL with ?lang when lang is known (explicit or detected), plain otherwise
    return PlainTextResponse((highlighted_url or plain_url) + "\n")


@app.get("/{paste_id}")
async def get_paste(paste_id: str, request: Request, db: Session = Depends(get_session)):
    paste_id = paste_id.upper()

    query_string = request.url.query

    paste = db.query(Paste).filter(Paste.id == paste_id, Paste.deleted_at.is_(None)).first()
    if not paste:
        raise HTTPException(status_code=404, detail=f"{paste_id} not found.")

    created_header = {"X-Pste-Created": paste.created_at.strftime("%Y-%m-%dT%H:%M:%SZ")}

    gcs_key = paste.gcs_key
    content_inline = paste.content
    lang = paste.lang

    if paste.single_view:
        if hard_delete_on_single_view():
            # Atomic DB delete — whoever wins the race (rowcount==1) serves the content
            result = db.execute(
                sa_delete(Paste).where(Paste.id == paste_id, Paste.deleted_at.is_(None))
            )
            db.commit()
            if result.rowcount == 0:
                raise HTTPException(status_code=404, detail=f"{paste_id} not found.")
            storage = get_storage()
            content = storage.retrieve(paste_id, gcs_key, content_inline)
            storage.delete(paste_id, gcs_key)
        else:
            # Atomic soft-delete claim — race loser gets rowcount==0 → 404
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            result = db.execute(
                sa_update(Paste)
                .where(Paste.id == paste_id, Paste.deleted_at.is_(None))
                .values(deleted_at=now, deleted_reason="single_view")
            )
            db.commit()
            if result.rowcount == 0:
                raise HTTPException(status_code=404, detail=f"{paste_id} not found.")
            storage = get_storage()
            content = storage.retrieve(paste_id, gcs_key, content_inline)
    else:
        storage = get_storage()
        content = storage.retrieve(paste_id, gcs_key, content_inline)

    if query_string and query_string.lower() != "none":
        return _render_highlighted(content, query_string, headers=created_header)

    return PlainTextResponse(content + "\n", headers=created_header)


def run():
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("pste_server.main:app", host="0.0.0.0", port=port, reload=False)
