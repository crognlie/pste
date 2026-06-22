import ipaddress
import threading
from collections import defaultdict, deque
from datetime import datetime, timezone

RATE_LIMIT = 10
WINDOW_SECONDS = 60

_PRIVATE_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("100.64.0.0/10"),  # Tailscale CGNAT
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),        # IPv6 ULA
]

_lock = threading.Lock()
_get_buckets: dict[str, deque] = defaultdict(deque)
_post_buckets: dict[str, deque] = defaultdict(deque)


def _is_private(ip_str: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip_str)
        return any(addr in net for net in _PRIVATE_NETWORKS)
    except ValueError:
        return False


def _client_ip(request) -> str:
    direct = request.client.host if request.client else None
    # Only trust proxy headers when the direct connection is from a known private proxy
    if direct and _is_private(direct):
        if xff := request.headers.get("x-forwarded-for"):
            return xff.split(",")[0].strip()
        if xri := request.headers.get("x-real-ip"):
            return xri.strip()
    return direct or "unknown"


def _check(bucket: deque, now: float) -> bool:
    cutoff = now - WINDOW_SECONDS
    while bucket and bucket[0] < cutoff:
        bucket.popleft()
    if len(bucket) >= RATE_LIMIT:
        return False
    bucket.append(now)
    return True


def is_allowed(request) -> bool:
    ip = _client_ip(request)
    if _is_private(ip):
        return True
    now = datetime.now(timezone.utc).timestamp()
    method = request.method.upper()
    with _lock:
        bucket = _get_buckets[ip] if method in ("GET", "HEAD") else _post_buckets[ip]
        return _check(bucket, now)


def reset():
    with _lock:
        _get_buckets.clear()
        _post_buckets.clear()
