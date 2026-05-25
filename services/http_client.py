from __future__ import annotations

import os

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


def build_session() -> requests.Session:
    session = requests.Session()
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


def get_timeout() -> float:
    raw = os.getenv("DATATUBE_HTTP_TIMEOUT", "15")
    try:
        return float(raw)
    except ValueError:
        return 15.0
