from __future__ import annotations

import unittest
from unittest.mock import patch

from services import http_client


REGISTRY_PROXIES = {
    "http": "http://127.0.0.1:63568",
    "https": "http://127.0.0.1:63568",
    "ftp": "http://127.0.0.1:63568",
}


class ProxyResolutionTest(unittest.TestCase):
    """Cover proxy resolution for an OS-level proxy plus a hostile no_proxy.

    A lone ``NO_PROXY=*`` alongside a configured system proxy used to send every
    request direct, which hangs against hosts that are only reachable through
    the proxy.
    """

    def setUp(self) -> None:
        # Force both TTL caches to expire so each test resolves from scratch.
        http_client._map_state.update({"checked_at": -1e9, "proxies": {}})
        http_client._probe_state.update({"checked_at": -1e9, "target": "", "alive": True})
        self.addCleanup(
            http_client._map_state.update, {"checked_at": -1e9, "proxies": {}}
        )
        self.addCleanup(
            http_client._probe_state.update,
            {"checked_at": -1e9, "target": "", "alive": True},
        )

    def _resolve(self, url: str, *, env: dict[str, str] | None = None, bypass: bool = False) -> str:
        """Resolve ``url`` against a fixed registry proxy and empty env proxies."""
        with patch.dict(http_client.os.environ, env or {}, clear=False), patch.object(
            http_client, "getproxies_registry", return_value=dict(REGISTRY_PROXIES)
        ), patch.object(
            http_client, "getproxies_environment", return_value={}
        ), patch.object(
            http_client, "should_bypass_proxies", return_value=bypass
        ):
            return http_client.resolve_proxy_url(url)

    def test_wildcard_no_proxy_does_not_suppress_the_system_proxy(self):
        for raw in ("*", "*,*", " * "):
            with self.subTest(no_proxy=raw):
                self.assertEqual(
                    "http://127.0.0.1:63568",
                    self._resolve("https://api.binance.com/x", env={"NO_PROXY": raw}),
                )

    def test_wildcard_is_stripped_but_specific_entries_survive(self):
        with patch.dict(http_client.os.environ, {"NO_PROXY": "*,example.com"}, clear=False):
            self.assertEqual("example.com", http_client._effective_no_proxy())
        with patch.dict(http_client.os.environ, {"NO_PROXY": "example.com"}, clear=False):
            self.assertIsNone(http_client._effective_no_proxy())
        with patch.dict(http_client.os.environ, {"NO_PROXY": "*.internal,*"}, clear=False):
            self.assertEqual("*.internal", http_client._effective_no_proxy())

    def test_loopback_is_never_proxied_whatever_no_proxy_says(self):
        targets = (
            "http://127.0.0.1:5001/api/health",
            "http://localhost:5001/x",
            "http://LOCALHOST:5001/x",
            "http://[::1]:5001/x",
            "http://127.53.0.1:9/x",
        )
        for raw in ("", "*", "*,localhost", "example.com"):
            for url in targets:
                with self.subTest(no_proxy=raw, url=url):
                    # bypass=False proves the loopback guard runs first: without
                    # it the registry proxy would be returned here.
                    self.assertEqual("", self._resolve(url, env={"NO_PROXY": raw}, bypass=False))

    def test_specific_no_proxy_host_still_goes_direct(self):
        self.assertEqual("", self._resolve("https://api.binance.com/x", bypass=True))

    def test_escape_hatch_restores_wildcard_no_proxy(self):
        env = {"NO_PROXY": "*", "DATATUBE_RESPECT_NO_PROXY_WILDCARD": "1"}
        with patch.dict(http_client.os.environ, env, clear=False):
            self.assertIsNone(http_client._effective_no_proxy())

    def test_registry_is_consulted_even_when_no_proxy_is_set(self):
        """getproxies() returns env *or* registry, so a lone NO_PROXY hid it."""
        with patch.object(
            http_client, "getproxies_environment", return_value={"no": "*"}
        ), patch.object(
            http_client, "getproxies_registry", return_value=dict(REGISTRY_PROXIES)
        ):
            self.assertEqual(
                {
                    "http": "http://127.0.0.1:63568",
                    "https": "http://127.0.0.1:63568",
                    "ftp": "http://127.0.0.1:63568",
                },
                http_client._proxy_map(),
            )

    def test_environment_proxy_overrides_the_registry(self):
        with patch.object(
            http_client,
            "getproxies_environment",
            return_value={"https": "http://10.0.0.9:8080", "no": "*"},
        ), patch.object(
            http_client, "getproxies_registry", return_value=dict(REGISTRY_PROXIES)
        ):
            proxies = http_client._proxy_map()
        self.assertEqual("http://10.0.0.9:8080", proxies["https"])
        self.assertEqual("http://127.0.0.1:63568", proxies["http"])

    def test_websocket_schemes_reuse_the_http_proxy_entries(self):
        self.assertEqual(
            "http://127.0.0.1:63568",
            self._resolve("wss://ws-subscriptions-clob.polymarket.com/ws/market"),
        )
        self.assertEqual("http://127.0.0.1:63568", self._resolve("ws://example.com/feed"))

    def test_unresolvable_url_reports_no_proxy(self):
        self.assertEqual("", http_client.resolve_proxy_url(""))


class ProxyLivenessTest(unittest.TestCase):
    def setUp(self) -> None:
        http_client._probe_state.update({"checked_at": -1e9, "target": "", "alive": True})
        self.addCleanup(
            http_client._probe_state.update,
            {"checked_at": -1e9, "target": "", "alive": True},
        )

    def test_unreachable_proxy_falls_back_to_a_direct_call(self):
        with patch.object(
            http_client, "resolve_proxy_url", return_value="http://127.0.0.1:1"
        ), patch.object(http_client, "_proxy_reachable", return_value=False):
            self.assertEqual({"http": None, "https": None}, http_client.proxies_for("https://x/"))

    def test_reachable_proxy_is_used(self):
        with patch.object(
            http_client, "resolve_proxy_url", return_value="http://127.0.0.1:63568"
        ), patch.object(http_client, "_proxy_reachable", return_value=True):
            self.assertEqual(
                {"http": "http://127.0.0.1:63568", "https": "http://127.0.0.1:63568"},
                http_client.proxies_for("https://x/"),
            )

    def test_liveness_result_is_cached_then_re_probed(self):
        with patch.object(http_client, "_proxy_reachable", return_value=True) as probe:
            self.assertTrue(http_client._proxy_alive("http://127.0.0.1:63568"))
            self.assertTrue(http_client._proxy_alive("http://127.0.0.1:63568"))
            self.assertEqual(1, probe.call_count)
            http_client._probe_state.update({"checked_at": -1e9})
            self.assertTrue(http_client._proxy_alive("http://127.0.0.1:63568"))
            self.assertEqual(2, probe.call_count)


class ProxyAwareSessionTest(unittest.TestCase):
    def test_session_injects_the_resolved_proxies(self):
        session = http_client.ProxyAwareSession()
        resolved = {"http": "http://127.0.0.1:63568", "https": "http://127.0.0.1:63568"}
        with patch.object(http_client, "proxies_for", return_value=dict(resolved)), patch.object(
            http_client.requests.Session, "request", return_value="sentinel"
        ) as inner:
            session.request("GET", "https://api.binance.com/x")
        self.assertEqual(resolved, inner.call_args.kwargs["proxies"])

    def test_caller_supplied_proxies_are_not_overwritten(self):
        session = http_client.ProxyAwareSession()
        explicit = {"https": "http://explicit.invalid:1"}
        with patch.object(http_client, "proxies_for", return_value={"https": "http://other:2"}), patch.object(
            http_client.requests.Session, "request", return_value="sentinel"
        ) as inner:
            session.request("GET", "https://api.binance.com/x", proxies=explicit)
        self.assertEqual(explicit, inner.call_args.kwargs["proxies"])


if __name__ == "__main__":
    unittest.main()
