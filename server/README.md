# pste-server

Self-hosted paste server inspired by [sprunge](http://sprunge.us). Pastes are world-readable; creating requires an API key.

**HTTPS is required in production.** API keys appear in the `Authorization` header and in the `/?key=<key>` query string used by the web form — both are exposed over plain HTTP. pste-server speaks plain HTTP on port 8000; use a reverse proxy or tunnel to terminate TLS. See [`examples/Caddyfile`](examples/Caddyfile) and [`examples/compose-cloudflare.yml`](examples/compose-cloudflare.yml).

## Quick start

```bash
pip install -e .
BASE_URL=https://pste.example.com pste-server

# Add your first API key — prints the bookmark URL directly
pste-admin key add --user alice
# -> https://pste.example.com/?key=AbCd1234...

# Set PSTE_URL on the client to that URL, then:
echo "hello" | pste
# -> https://pste.example.com/AB1234
pste AB1234
# -> hello
```

See [`examples/`](examples/) for Docker Compose, Cloudflare Tunnel, and cloud deployment configurations.

## API

```
GET  /              Help page (add ?key=<key> for the paste web form)
POST /              Create paste (requires Authorization: Bearer <key>)
GET  /<id>          Fetch paste as plain text
GET  /<id>?<lang>   Fetch with Pygments syntax highlighting + copy button
```

**Creating pastes:**

| Field | Type | Description |
|---|---|---|
| `pste` | string | Paste content (required) |
| `lang` | string | Pygments lexer name for syntax highlighting |
| `auto_detect` | `1` | Auto-detect language (Pygments, >0.5 confidence threshold) |
| `single_view` | `1` | Delete after first read |
| `expires_at` | ISO8601 UTC | Absolute expiry timestamp |
| `expires_in_n` | integer | Expiry amount (used with `expires_in_unit`) |
| `expires_in_unit` | H/D/W/M | Expiry unit: hours, days, weeks, minutes |

`lang` and `auto_detect` are mutually exclusive — if `lang` is provided, auto-detection is skipped. `expires_at` and `expires_in_n`/`expires_in_unit` are also mutually exclusive.

**Fetching pastes:**

- `GET /<id>` — always plain text, regardless of stored lang
- `GET /<id>?<lang>` — Pygments-highlighted HTML with table line numbers (line numbers have `user-select: none` so Ctrl-A copies only code) and a Copy button
- `GET /<id>?none` — plain text (same as bare GET)

## Web form

Open `/?key=<key>` in a browser to use the paste web form. The key is embedded in the bookmark URL; paste it from `pste-admin key add` output. The form includes:

- Textarea for paste content
- Single-view checkbox
- Expiry controls (number + H/D/W/M dropdown)
- Language dropdown (auto-detect default, 27 common lexers)

When submitted with **language auto-detect** (default), if Pygments identifies the language with >0.5 confidence the result page shows both the plain URL and a highlighted URL. When a **specific language** is selected the result shows only the highlighted `?<lang>` URL.

## Managing API keys

```bash
# Add a key (prints the full bookmark URL)
pste-admin key add --user alice --notes "personal laptop"

# Specify your own key value (must be [A-Za-z0-9])
pste-admin key add --key MySecretKey --user alice

# List all keys
pste-admin key list

# Revoke by key value (immediate, no confirmation)
pste-admin key revoke --key <key-value>

# Revoke all keys for a user or matching notes (lists keys, requires y to confirm)
pste-admin key revoke --user alice
pste-admin key revoke --notes "old laptop"

# Update key metadata
pste-admin key set --user alice --notes "rotated 2026-07"
pste-admin key set --key <key-value> --disabled true

# List pastes (shows ID, created, lang, key, deleted status)
pste-admin paste list --user alice
```

Keys take effect immediately — no restart required.

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `BASE_URL` | `http://localhost:8000` | Public URL used in paste links and key add output |
| `PORT` | `8000` | Listen port |
| `STORAGE_BACKEND` | `sqlite` | `sqlite`, `postgresql`, or `gcs` |
| `SQLITE_PATH` | `./data/pste.db` | SQLite DB path |
| `DATABASE_URL` | — | PostgreSQL connection string |
| `GCS_BUCKET` | — | GCS bucket name |
| `MAX_PASTE_BYTES` | `1048576` | Max paste size (bytes) |
| `DARK_MODE` | `false` | Use `github-dark` as the highlight style; default (unset) uses `default` (light) |
| `HIGHLIGHT_STYLE` | — | Pin to any [Pygments style](https://pygments.org/styles/) name, ignoring `DARK_MODE` |

### Deletion

By default, expired and single-view pastes are **soft-deleted** — `deleted_at` is set and they become inaccessible, but the row is retained. The variables below control hard deletion.

| Variable | Default | Description |
|---|---|---|
| `DELETE_ON_EXPIRE` | `false` | Hard-delete immediately on expiry |
| `DELETE_ON_SINGLE_VIEW` | `false` | Hard-delete immediately on first view |
| `DELETE_AFTER_EXPIRE` | `7D` | Hard-delete soft-deleted expired rows after this duration |
| `DELETE_AFTER_SINGLE_VIEW` | `7D` | Hard-delete soft-deleted single-view rows after this duration |

Duration format for `DELETE_AFTER_*`: integer + `H` (hours), `D` (days), `W` (weeks), or `M` (minutes). Set to empty string (`DELETE_AFTER_EXPIRE=`) to disable deferred hard-deletion entirely. `DELETE_AFTER_*` runs on a 30-minute cycle, independently of `DELETE_ON_*`.
