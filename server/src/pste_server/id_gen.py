import logging
import secrets
import string

ALPHABET = string.ascii_uppercase + string.digits  # [A-Z0-9], 36 chars

_id_length_cache: int | None = None


def get_id_length(db) -> int:
    global _id_length_cache
    if _id_length_cache is not None:
        return _id_length_cache
    from pste_server.models import ServerState
    row = db.query(ServerState).filter_by(key="id_length").first()
    if row is None:
        from sqlalchemy.exc import IntegrityError
        try:
            row = ServerState(key="id_length", value="6")
            db.add(row)
            db.commit()
        except IntegrityError:
            db.rollback()
            row = db.query(ServerState).filter_by(key="id_length").first()
    _id_length_cache = int(row.value)
    return _id_length_cache


def bump_id_length_if_needed(total_paste_count: int, db) -> None:
    global _id_length_cache
    from pste_server.models import ServerState
    length = get_id_length(db)
    threshold = int((36 ** length) * 0.01)
    if total_paste_count >= threshold:
        new_length = length + 1
        row = db.query(ServerState).filter_by(key="id_length").first()
        if row:
            row.value = str(new_length)
        else:
            db.add(ServerState(key="id_length", value=str(new_length)))
        db.commit()
        _id_length_cache = new_length
        logging.warning(
            "Paste count %d reached 1%% of 36^%d (%d); bumping ID length to %d",
            total_paste_count, length, threshold, new_length,
        )


def generate_id(length: int) -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(length))
