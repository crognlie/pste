import os
import re
import secrets
import string
import sys
from datetime import datetime

import click
from sqlalchemy.orm import sessionmaker

from pste_server.models import Key, Paste, get_engine

_KEY_RE = re.compile(r"^[A-Za-z0-9]+$")
_KEY_ALPHABET = string.ascii_letters + string.digits


def _generate_key(length: int = 32) -> str:
    return "".join(secrets.choice(_KEY_ALPHABET) for _ in range(length))


def _get_db():
    Session = sessionmaker(bind=get_engine())
    return Session()


def _fmt_dt(dt: datetime | None) -> str:
    if dt is None:
        return ""
    return dt.strftime("%Y-%m-%d %H:%M:%S")


@click.group()
def cli():
    """pste server administration tool."""


@cli.group()
def key():
    """Manage API keys."""


@key.command("add")
@click.option("--key", "key_val", default=None, help="Key value (generated if omitted; must be [A-Za-z0-9])")
@click.option("--user", default=None, help="Human identifier (name, email, etc.)")
@click.option("--notes", default=None, help="Freeform notes")
def key_add(key_val, user, notes):
    """Generate and store a new API key."""
    if key_val is not None:
        if not _KEY_RE.match(key_val):
            click.echo("Error: --key must contain only [A-Za-z0-9]", err=True)
            sys.exit(1)
    else:
        key_val = _generate_key()
    db = _get_db()
    try:
        if db.query(Key).filter(Key.key == key_val).first():
            click.echo("Error: key already exists", err=True)
            sys.exit(1)
        k = Key(key=key_val, user=user, notes=notes, disabled=False)
        db.add(k)
        db.commit()
        base_url = os.environ.get("BASE_URL", "http://localhost:8000").rstrip("/")
        click.echo(f"{base_url}/?key={key_val}")
    finally:
        db.close()


@key.command("list")
def key_list():
    """List all API keys."""
    db = _get_db()
    try:
        keys = db.query(Key).order_by(Key.created_at).all()
        if not keys:
            click.echo("No keys found.")
            return
        fmt = "{:<44} {:<20} {:<8} {:<19} {}"
        click.echo(fmt.format("KEY", "USER", "DISABLED", "CREATED", "NOTES"))
        click.echo("-" * 100)
        for k in keys:
            click.echo(fmt.format(
                k.key[:43],
                (k.user or "")[:19],
                "yes" if k.disabled else "no",
                _fmt_dt(k.created_at),
                k.notes or "",
            ))
    finally:
        db.close()


@key.command("set")
@click.option("--key", "key_val", default=None, help="Match by key value")
@click.option("--user", "user_match", default=None, help="Match by user (updates all matching)")
@click.option("--set-user", default=None, help="New user value")
@click.option("--notes", default=None, help="New notes value")
@click.option("--disabled", default=None, type=click.Choice(["true", "false"]), help="Enable/disable key")
def key_set(key_val, user_match, set_user, notes, disabled):
    """Modify key fields. Match by --key or --user."""
    if not key_val and not user_match:
        click.echo("Error: must provide --key or --user to match", err=True)
        sys.exit(1)

    db = _get_db()
    try:
        q = db.query(Key)
        if key_val:
            q = q.filter(Key.key == key_val)
        if user_match:
            q = q.filter(Key.user == user_match)
        keys = q.all()
        if not keys:
            click.echo("No matching keys found.", err=True)
            sys.exit(1)

        for k in keys:
            if set_user is not None:
                k.user = set_user
            if notes is not None:
                k.notes = notes
            if disabled is not None:
                k.disabled = disabled == "true"
        db.commit()
        click.echo(f"Updated {len(keys)} key(s).")
    finally:
        db.close()


@key.command("revoke")
@click.option("--key", "key_val", default=None, help="Revoke key by exact value")
@click.option("--user", "user_match", default=None, help="Revoke all keys for this user (with confirmation)")
@click.option("--notes", "notes_match", default=None, help="Revoke all keys with these notes (with confirmation)")
def key_revoke(key_val, user_match, notes_match):
    """Disable one or more keys (soft revoke)."""
    if not any([key_val, user_match, notes_match]):
        click.echo("Error: must provide --key, --user, or --notes", err=True)
        sys.exit(1)

    db = _get_db()
    try:
        q = db.query(Key)
        if key_val:
            q = q.filter(Key.key == key_val)
        if user_match:
            q = q.filter(Key.user == user_match)
        if notes_match:
            q = q.filter(Key.notes == notes_match)
        keys = q.all()

        if not keys:
            click.echo("No matching keys found.", err=True)
            sys.exit(1)

        # Require confirmation when matching by user or notes (potentially bulk)
        if user_match or notes_match:
            click.echo(f"Keys to revoke ({len(keys)}):")
            for k in keys:
                click.echo(f"  {k.key[:16]}...  user={k.user or ''}  notes={k.notes or ''}")
            if not click.confirm(f"Revoke {len(keys)} key(s)?", default=False):
                click.echo("Cancelled.")
                return

        for k in keys:
            k.disabled = True
        db.commit()
        click.echo(f"Revoked {len(keys)} key(s).")
    finally:
        db.close()


@cli.group()
def paste():
    """Manage pastes."""


@paste.command("list")
@click.option("--user", default=None, help="Filter by creating user")
@click.option("--key", "key_val", default=None, help="Filter by API key")
def paste_list(user, key_val):
    """List pastes."""
    db = _get_db()
    try:
        q = db.query(Paste)
        if key_val:
            q = q.filter(Paste.created_by == key_val)
        if user:
            q = q.join(Key, Paste.created_by == Key.key).filter(Key.user == user)
        pastes = q.order_by(Paste.created_at.desc()).limit(200).all()
        if not pastes:
            click.echo("No pastes found.")
            return
        fmt = "{:<10} {:<19} {:<10} {:<44} {:<19} {}"
        click.echo(fmt.format("ID", "CREATED", "LANG", "CREATED_BY", "DELETED", "REASON"))
        click.echo("-" * 120)
        for p in pastes:
            click.echo(fmt.format(
                p.id,
                _fmt_dt(p.created_at),
                p.lang or "",
                (p.created_by or "")[:43],
                _fmt_dt(p.deleted_at),
                p.deleted_reason or "",
            ))
    finally:
        db.close()


if __name__ == "__main__":
    cli()
