import abc
import os
import threading


class StorageBackend(abc.ABC):
    @abc.abstractmethod
    def store(self, paste_id: str, content: str) -> str | None:
        """Store content; return gcs_key if using GCS, else None."""

    @abc.abstractmethod
    def retrieve(self, paste_id: str, gcs_key: str | None, db_content: str | None) -> str:
        """Return paste content as a string."""

    def delete(self, paste_id: str, gcs_key: str | None) -> None:
        """Delete stored content. No-op for SQL backends."""


class SqlStorage(StorageBackend):
    def store(self, paste_id: str, content: str) -> None:
        return None

    def retrieve(self, paste_id: str, gcs_key: str | None, db_content: str | None) -> str:
        return db_content or ""


class GcsStorage(StorageBackend):
    def __init__(self):
        try:
            from google.cloud import storage as gcs
        except ImportError:
            raise ImportError(
                "GCS storage requires the 'gcs' extra: pip install pste-server[gcs]"
            )
        self._bucket_name = os.environ["GCS_BUCKET"]
        self._client = gcs.Client()
        self._bucket = self._client.bucket(self._bucket_name)

    def store(self, paste_id: str, content: str) -> str:
        blob = self._bucket.blob(paste_id)
        blob.upload_from_string(content.encode("utf-8"), content_type="text/plain; charset=UTF-8")
        return paste_id

    def retrieve(self, paste_id: str, gcs_key: str | None, db_content: str | None) -> str:
        key = gcs_key or paste_id
        blob = self._bucket.blob(key)
        return blob.download_as_text(encoding="utf-8")

    def delete(self, paste_id: str, gcs_key: str | None) -> None:
        key = gcs_key or paste_id
        self._bucket.blob(key).delete()


_backend: StorageBackend | None = None
_backend_lock = threading.Lock()


def get_storage() -> StorageBackend:
    global _backend
    if _backend is not None:
        return _backend
    with _backend_lock:
        if _backend is None:
            backend_name = os.environ.get("STORAGE_BACKEND", "sqlite")
            if backend_name in ("sqlite", "postgresql"):
                _backend = SqlStorage()
            elif backend_name == "gcs":
                _backend = GcsStorage()
            else:
                raise ValueError(f"Unknown STORAGE_BACKEND: {backend_name}")
    return _backend
