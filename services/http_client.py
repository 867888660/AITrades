from __future__ import annotations

import logging
import os
import socket
import threading
import time
from ipaddress import ip_address
from urllib.parse import urlparse
from urllib.request import getproxies_environment, getproxies_registry

import requests
from requests.adapters import HTTPAdapter
from requests.utils import should_bypass_proxies
from urllib3.util.retry import Retry


LOGGER = logging.getLogger(__name__)

# Passing an explicit None per request is the only reliable bypass: assigning
# session.proxies = {"https": None} does not work, because
# merge_environment_settings uses setdefault against the environment proxies.
_NO_PROXY = {"http": None, "https": None}

# WebSocket schemes reuse the HTTP proxy entries.
_SCHEME_ALIASES = {"ws": "http", "wss": "https"}

_PROXY_MAP_TTL_SECONDS = 30.0
_PROXY_PROBE_TTL_SECONDS = 60.0
_PROXY_PROBE_TIMEOUT_SECONDS = 0.3

_map_lock = threading.Lock()
_map_state: dict[str, object] = {"checked_at": 0.0, "proxies": {}}

_probe_lock = threading.Lock()
_probe_state: dict[str, object] = {"checked_at": 0.0, "target": "", "alive": True}


def _respect_no_proxy_wildcard() -> bool:
    """Whether a bare ``NO_PROXY=*`` should be obeyed.

    Off by default: see :func:`_effective_no_proxy`.
    """
    return str(os.getenv("DATATUBE_RESPECT_NO_PROXY_WILDCARD", "")).strip().lower() in {
        "1",
        "true",
        "yes",
    }


def _effective_no_proxy() -> str | None:
    """Return the no_proxy value to apply, neutralising a global wildcard.

    ``NO_PROXY=*`` disables proxying for every host.  When an OS-level proxy is
    also configured the two settings contradict each other, and the wildcard
    wins by accident -- outbound calls then go direct and hang against hosts
    that are only reachable through the proxy.  A bare ``*`` entry is therefore
    dropped and the remaining hosts are returned explicitly, which also stops
    requests from re-reading the environment.  ``None`` means "no wildcard was
    present, use the environment as-is".
    """
    if _respect_no_proxy_wildcard():
        return None
    raw = os.getenv("no_proxy") or os.getenv("NO_PROXY") or ""
    entries = [item.strip() for item in raw.split(",") if item.strip()]
    # Only a bare "*" is dropped; suffix patterns such as "*.internal" are real
    # rules and must survive.
    kept = [item for item in entries if item != "*"]
    if len(kept) != len(entries):
        return ",".join(kept)
    return None


def _proxy_map() -> dict[str, str]:
    """Return the scheme -> proxy-URL mapping to use for outbound requests.

    ``urllib.getproxies()`` returns ``getproxies_environment() or
    getproxies_registry()``, so any proxy variable in the environment -- even a
    lone ``NO_PROXY`` -- makes the first call truthy and the Windows registry is
    never consulted.  The two sources are merged explicitly here, with the
    environment taking precedence for schemes it actually defines.
    """
    now = time.monotonic()
    with _map_lock:
        if now - float(_map_state["checked_at"]) < _PROXY_MAP_TTL_SECONDS:
            return dict(_map_state["proxies"])  # type: ignore[arg-type]
    env = {key: value for key, value in getproxies_environment().items() if key != "no" and value}
    merged: dict[str, str] = {}
    try:
        merged.update({key: value for key, value in getproxies_registry().items() if value})
    except Exception:  # pragma: no cover - non-Windows platforms
        pass
    merged.update(env)
    with _map_lock:
        _map_state.update({"checked_at": now, "proxies": dict(merged)})
    return merged


def _is_loopback(hostname: str) -> bool:
    """Whether ``hostname`` addresses the local machine."""
    host = str(hostname or "").strip().strip("[]").lower()
    if not host:
        return False
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def resolve_proxy_url(url: str) -> str:
    """Return the proxy URL to use for ``url``, or ``""`` for a direct call.

    Honours no_proxy and the registry's ProxyOverride rules, so intranet targets
    stay direct.  Shared by the HTTP session and the WebSocket client so both
    agree on how traffic is routed.
    """
    target = str(url or "")
    if not target:
        return ""
    parsed = urlparse(target)
    # Loopback is never proxied, whatever the OS or no_proxy settings say.
    # Relying on ProxyOverride alone is unsafe: passing an explicit no_proxy
    # list makes requests consult the environment instead of the registry, so a
    #127.* rule that lives only in ProxyOverride would silently stop applying
    # and the app would proxy calls to its own gateway.
    if _is_loopback(parsed.hostname or ""):
        return ""
    try:
        if should_bypass_proxies(target, no_proxy=_effective_no_proxy()):
            return ""
    except Exception:
        return ""
    scheme = parsed.scheme.lower() or "https"
    return str(_proxy_map().get(_SCHEME_ALIASES.get(scheme, scheme)) or "")


def _proxy_reachable(proxy_url: str) -> bool:
    parsed = urlparse(proxy_url if "://" in proxy_url else f"http://{proxy_url}")
    host = parsed.hostname
    if not host:
        return False
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=_PROXY_PROBE_TIMEOUT_SECONDS):
            return True
    except OSError:
        return False


def _proxy_alive(proxy_url: str) -> bool:
    """Report whether ``proxy_url`` is currently accepting connections.

    A proxy client that exited without clearing the OS-level setting otherwise
    makes every outbound call fail, including calls to hosts that are reachable
    directly.  The result is cached briefly so a recovered proxy is picked up
    again without restarting the process.
    """
    now = time.monotonic()
    with _probe_lock:
        fresh = now - float(_probe_state["checked_at"]) < _PROXY_PROBE_TTL_SECONDS
        if fresh and proxy_url == _probe_state["target"]:
            return bool(_probe_state["alive"])
        was_alive = bool(_probe_state["alive"])
    alive = _proxy_reachable(proxy_url)
    with _probe_lock:
        _probe_state.update({"checked_at": now, "target": proxy_url, "alive": alive})
    if was_alive and not alive:
        LOGGER.warning(
            "proxy %s is not accepting connections; using direct connections "
            "until it recovers",
            proxy_url,
        )
    elif alive and not was_alive:
        LOGGER.info("proxy %s is reachable again; resuming proxied requests", proxy_url)
    return alive


def proxies_for(url: str) -> dict[str, str | None]:
    """Return the ``proxies`` mapping to pass to requests for ``url``."""
    proxy_url = resolve_proxy_url(url)
    if not proxy_url or not _proxy_alive(proxy_url):
        return dict(_NO_PROXY)
    return {"http": proxy_url, "https": proxy_url}


class ProxyAwareSession(requests.Session):
    """Session that resolves proxies from the environment *and* the registry.

    Proxies are injected per request because the environment cannot be
    overridden through ``session.proxies``; ``trust_env`` stays on so unrelated
    settings such as the CA bundle keep working.
    """

    def request(self, method, url, **kwargs):  # type: ignore[override]
        if kwargs.get("proxies") is None:
            kwargs["proxies"] = proxies_for(str(url))
        return super().request(method, url, **kwargs)


def build_session() -> requests.Session:
    session = ProxyAwareSession()
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        status=3,
        backoff_factor=0.4,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=20, pool_maxsize=20)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({"User-Agent": "polymarket-datatube-web/1.0"})
    session.trust_env = True
    return session


SESSION = build_session()


def proxy_diagnostics(url: str = "https://api.binance.com") -> dict[str, object]:
    """Describe how outbound requests to ``url`` are currently routed."""
    raw_no_proxy = os.getenv("no_proxy") or os.getenv("NO_PROXY") or ""
    proxy = resolve_proxy_url(url)
    reachable = _proxy_alive(proxy) if proxy else None
    if not proxy:
        route = "DIRECT_NO_PROXY_FOR_TARGET"
    elif reachable:
        route = "PROXY"
    else:
        route = "DIRECT_PROXY_UNREACHABLE"
    return {
        "target": url,
        "configured_proxy": proxy,
        "proxy_reachable": reachable,
        "route": route,
        "no_proxy_env": raw_no_proxy,
        "no_proxy_wildcard_ignored": bool(raw_no_proxy) and _effective_no_proxy() == "",
        "available_proxies": _proxy_map(),
    }


def get_timeout() -> float:
    raw = os.getenv("DATATUBE_HTTP_TIMEOUT", "15")
    try:
        return float(raw)
    except ValueError:
        return 15.0
