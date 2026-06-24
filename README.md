# pste

[![Docker](https://img.shields.io/github/actions/workflow/status/crognlie/pste/docker.yml?branch=main&label=docker)](https://github.com/crognlie/pste/actions/workflows/docker.yml)
[![pste-server on PyPI](https://img.shields.io/pypi/v/pste-server?label=pste-server)](https://pypi.org/project/pste-server/)
[![pste on PyPI](https://img.shields.io/pypi/v/pste?label=pste)](https://pypi.org/project/pste/)
[![License](https://img.shields.io/github/license/crognlie/pste)](LICENSE)

A self-hosted pastebin with a CLI client. Inspired by [sprunge](https://github.com/rupa/sprunge) — pipe text in, get a URL back. Supports syntax highlighting, single-view pastes, expiry, and multiple storage backends (SQLite, PostgreSQL, GCS).

**GitHub:** [crognlie/pste](https://github.com/crognlie/pste) · **Docker:** `ghcr.io/crognlie/pste:latest`

> Looking for a self-hosted sprunge alternative, a lightweight pastebin server, or a command-line paste tool? This is it.

## Why

Sprunge was the perfect pastebin — pipe in, get a URL, done. For years after it went down I was still reaching for it regularly. Running something that lightweight as a public service isn't economically viable, but hosting it yourself costs almost nothing. This is that: no GUI cruft, no accounts, just a URL you can share.

## Examples

```
~$ echo "hello world" | pste
https://pste.example.com/AB1234
~$ pste AB1234
hello world
~$ echo "hello" | pste -s
https://pste.example.com/AB1235        # only viewable once
~$ echo "hello" | pste -e 7D
https://pste.example.com/AB1236        # expires in 7 days
~$ pste -l go < main.go
https://pste.example.com/AB1237?go
~$ pste -l < data.json
https://pste.example.com/AB1238?json   # auto-detected
```

## Quick start

```bash
# Server — Docker (recommended)
docker run -e BASE_URL=http://localhost:8000 -p 8000:8000 -v pste-data:/app/data ghcr.io/crognlie/pste:latest

# Server — PyPI
pip install pste-server
BASE_URL=http://localhost:8000 pste-server

# Client
pip install pste
export PSTE_URL="http://localhost:8000/?key=YOUR_API_KEY"  # from: pste-admin key add
```

## Server setup

Reverse proxy configs and compose examples are in [server/examples](https://github.com/crognlie/pste/tree/main/server/examples) (Caddy, nginx, Cloudflare Tunnel, GCP/AWS/Azure).

Key env vars:

| Variable | Default | Description |
|---|---|---|
| `BASE_URL` | `http://localhost:8000` | Public URL for paste links |
| `STORAGE_BACKEND` | `sqlite` | `sqlite`, `postgresql`, or `gcs` |
| `DELETE_ON_EXPIRE` | `false` | Hard-delete on expiry instead of soft-delete |
| `DELETE_ON_SINGLE_VIEW` | `false` | Hard-delete on first view instead of soft-delete |

API key management:

```bash
pste-admin key add                  # generate key, print bookmark URL
pste-admin key list
pste-admin key revoke --key <key>
pste-admin paste list
```

## Deployment

The Docker image is published to GHCR on every push to `main`: `ghcr.io/crognlie/pste:latest`. Compose examples using the published image are in `server/examples/`. The server runs as a single process; start it with a process manager (systemd, Docker restart policy, etc.) rather than multiple workers.

---

> **Note:** pste is designed for single-process, single-server deployments. Rate limiting and paste-ID length state are in-process; running multiple uvicorn workers or multiple server instances will result in independent rate limit windows and stale ID-length caches. It is not designed for high-availability or horizontally-scaled production use.
