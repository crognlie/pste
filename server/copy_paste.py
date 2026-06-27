#!/usr/bin/env python3
"""
Copy a paste from one slug to another.

Usage:
    python copy_paste.py <SRC> <DST> [--force]
    docker exec <container> python /app/copy_paste.py <SRC> <DST> [--force]

Environment variables are inherited from the server (STORAGE_BACKEND, SQLITE_PATH,
DATABASE_URL, GCS_BUCKET, etc.).
"""
import argparse
import os
import sys

from pste_server.models import Paste, get_engine
from pste_server.storage import get_storage
from sqlalchemy.orm import sessionmaker


def main():
    parser = argparse.ArgumentParser(description="Copy a paste from one slug to another.")
    parser.add_argument("src", help="Source paste slug")
    parser.add_argument("dst", help="Destination paste slug")
    parser.add_argument("--force", action="store_true", help="Overwrite destination if it exists")
    args = parser.parse_args()

    src = args.src.upper()
    dst = args.dst.upper()

    if src == dst:
        print("Error: source and destination are the same", file=sys.stderr)
        sys.exit(1)

    Session = sessionmaker(bind=get_engine())
    db = Session()
    try:
        src_paste = db.query(Paste).filter(Paste.id == src, Paste.deleted_at.is_(None)).first()
        if not src_paste:
            print(f"Error: source paste {src!r} not found", file=sys.stderr)
            sys.exit(1)

        dst_paste = db.query(Paste).filter(Paste.id == dst, Paste.deleted_at.is_(None)).first()
        if dst_paste and not args.force:
            print(f"Error: destination paste {dst!r} already exists (use --force to overwrite)", file=sys.stderr)
            sys.exit(1)

        storage = get_storage()
        content = storage.retrieve(src, src_paste.gcs_key, src_paste.content)

        if dst_paste:
            old_gcs_key = dst_paste.gcs_key
            gcs_key = storage.store(dst, content)
            if old_gcs_key and old_gcs_key != gcs_key:
                try:
                    storage.delete(dst, old_gcs_key)
                except Exception:
                    pass
            dst_paste.content = content if gcs_key is None else None
            dst_paste.gcs_key = gcs_key
            dst_paste.lang = src_paste.lang
            dst_paste.single_view = src_paste.single_view
            dst_paste.expires_at = src_paste.expires_at
            dst_paste.size_bytes = src_paste.size_bytes
        else:
            gcs_key = storage.store(dst, content)
            db.add(Paste(
                id=dst,
                created_by=src_paste.created_by,
                expires_at=src_paste.expires_at,
                single_view=src_paste.single_view,
                lang=src_paste.lang,
                size_bytes=src_paste.size_bytes,
                content=content if gcs_key is None else None,
                gcs_key=gcs_key,
            ))

        db.commit()
        base_url = os.environ.get("BASE_URL", "http://localhost:8000").rstrip("/")
        print(f"{base_url}/{dst}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
