import asyncio

from miniclaw.tools.browser_tool import (
    _guard_navigation,
    _is_blocked_ip,
    _validate_public_dns,
    _validate_url,
)


class _Route:
    def __init__(self):
        self.action = None

    async def abort(self, reason):
        self.action = ("abort", reason)

    async def continue_(self):
        self.action = ("continue", None)


class _Request:
    def __init__(self, url, navigation=True):
        self.url = url
        self._navigation = navigation

    def is_navigation_request(self):
        return self._navigation


def test_blocks_private_and_ipv6_loopback_literals():
    assert _is_blocked_ip("127.0.0.1")
    assert _is_blocked_ip("::1")
    assert _is_blocked_ip("fe80::1")
    assert not _is_blocked_ip("8.8.8.8")


def test_url_validation_rejects_ipv6_loopback():
    url, error = _validate_url("http://[::1]/admin")

    assert url is None
    assert "blocked host" in error


def test_dns_validation_rejects_localhost_resolution():
    error = asyncio.run(_validate_public_dns("localhost"))

    assert error is not None
    assert "non-public" in error


def test_navigation_guard_blocks_private_redirect():
    route = _Route()

    asyncio.run(_guard_navigation(route, _Request("http://127.0.0.1/private")))

    assert route.action == ("abort", "blockedbyclient")


def test_navigation_guard_leaves_non_navigation_requests_alone():
    route = _Route()

    asyncio.run(_guard_navigation(route, _Request("http://127.0.0.1/icon.png", navigation=False)))

    assert route.action == ("continue", None)
