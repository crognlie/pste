import pytest

import pste_server.ratelimit as rl


@pytest.fixture(autouse=True)
def _clean():
    rl.reset()
    yield
    rl.reset()


class _FakeRequest:
    def __init__(self, ip, method="GET"):
        self.headers = {}
        self.method = method

        class _Client:
            host = ip
        self.client = _Client()


def test_allows_requests_under_limit():
    req = _FakeRequest("1.2.3.4", "GET")
    for _ in range(rl.RATE_LIMIT):
        assert rl.is_allowed(req) is True


def test_blocks_after_limit():
    req = _FakeRequest("1.2.3.4", "GET")
    for _ in range(rl.RATE_LIMIT):
        rl.is_allowed(req)
    assert rl.is_allowed(req) is False


def test_get_and_post_limits_are_independent():
    get_req = _FakeRequest("5.6.7.8", "GET")
    post_req = _FakeRequest("5.6.7.8", "POST")
    for _ in range(rl.RATE_LIMIT):
        rl.is_allowed(get_req)
    # GET limit exhausted, POST should still be allowed
    assert rl.is_allowed(post_req) is True


def test_different_ips_have_independent_limits():
    req_a = _FakeRequest("1.1.1.1", "GET")
    req_b = _FakeRequest("2.2.2.2", "GET")
    for _ in range(rl.RATE_LIMIT):
        rl.is_allowed(req_a)
    assert rl.is_allowed(req_a) is False
    assert rl.is_allowed(req_b) is True


@pytest.mark.parametrize("ip", [
    "127.0.0.1",
    "10.0.0.1",
    "10.255.255.255",
    "172.16.0.1",
    "172.31.255.255",
    "192.168.1.100",
    "100.64.0.1",       # Tailscale CGNAT
    "100.127.255.255",  # Tailscale CGNAT upper bound
    "::1",
])
def test_private_ips_are_exempt(ip):
    req = _FakeRequest(ip, "POST")
    for _ in range(rl.RATE_LIMIT + 5):
        assert rl.is_allowed(req) is True


def test_xff_header_used_for_ip():
    req = _FakeRequest("10.0.0.1", "GET")  # proxy IP (private, would be exempt)
    req.headers["x-forwarded-for"] = "1.2.3.4, 10.0.0.1"
    for _ in range(rl.RATE_LIMIT):
        rl.is_allowed(req)
    assert rl.is_allowed(req) is False  # 1.2.3.4 is rate-limited


def test_x_real_ip_header_used_for_ip():
    req = _FakeRequest("10.0.0.1", "GET")
    req.headers["x-real-ip"] = "9.9.9.9"
    for _ in range(rl.RATE_LIMIT):
        rl.is_allowed(req)
    assert rl.is_allowed(req) is False
