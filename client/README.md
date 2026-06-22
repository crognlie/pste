# pste

Command-line client for [pste-server](../server/), a self-hosted paste server inspired by [sprunge](http://sprunge.us).

## Installation

```bash
pip install pste
# or
pipx install pste
```

## Configuration

Set `PSTE_URL` to the bookmark URL printed by `pste-admin key add` on the server — the same URL you'd save in a browser to use the web form:

```bash
export PSTE_URL="https://pste.example.com/?key=your-api-key"
```

## Usage

```bash
# Create a paste from stdin
echo "hello world" | pste
pste < file.txt
cat file.txt | pste

# Create with options
echo "hello" | pste -s              # single-view (deleted after first read)
echo "hello" | pste -e 7d           # expires in 7 days
echo "hello" | pste -e 2W           # expires in 2 weeks
pste -l python < script.py          # explicit syntax highlighting language
pste -l < script.py                 # auto-detect language (Pygments, >0.5 confidence)

# Fetch a paste (by ID or full URL)
pste AB1234
pste https://pste.example.com/AB1234
pste https://pste.example.com/AB1234?python   # ?lang is stripped; always fetches plain text
```

## Language flag behaviour

| Invocation | What is sent | URL returned |
|---|---|---|
| `pste` (no `-l`) | nothing | plain URL |
| `pste -l python` | `lang=python` | `https://host/AB1234?python` |
| `pste -l` (no value) | `auto_detect=1` | `https://host/AB1234?python` if detected, else plain URL |

When a `?<lang>` URL is returned, opening it in a browser shows Pygments-highlighted HTML with table line numbers (Ctrl-A selects only the code) and a Copy button. Fetching via `pste` always strips the `?lang` and returns plain text.

## Expiry format

`-e`/`--expire` accepts an integer followed by a unit:
- `H` — hours
- `D` — days
- `W` — weeks
- `M` — minutes

Examples: `24H`, `7D`, `2W`, `30M`
