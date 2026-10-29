"""Abruf abonnierter Datenangebote aus der Mobilithek (Pull per HTTPS mit Client-Zertifikat).

Schnittstelle laut Technischer Schnittstellenbeschreibung der Mobilithek:
``GET https://mobilithek.info:8443/mobilithek/api/v1.0/subscription?subscriptionID=<ID>``
mit ``Accept-Encoding: gzip`` (Pflicht) und optional ``If-Modified-Since``.
Antworten: 200 Daten, 204 Puffer leer, 304 nichts Neueres.

Die Mobilithek liefert Pakete in Eingangsreihenfolge. Deshalb wird so lange
mit dem jeweils letzten ``Last-Modified`` nachgefragt, bis nichts Neueres
mehr kommt; das zuletzt erhaltene Paket ist der aktuelle Stand.
"""

from __future__ import annotations

import gzip
import json
import logging
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

BASE_URL = "https://mobilithek.info:8443/mobilithek/api/v1.0/subscription"
MAX_ROUNDS = 25
TIMEOUT = 120
RETRIES = 3
RETRY_DELAY = 5

_LOGGER = logging.getLogger(__name__)


class FetchError(RuntimeError):
    """Abruf eines Datenangebots ist fehlgeschlagen."""


@dataclass
class FetchResult:
    body: bytes
    last_modified: str | None
    from_cache: bool


def build_ssl_context(cert_file: str, key_file: str) -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certfile=cert_file, keyfile=key_file)
    return context


def _decompress(body: bytes) -> bytes:
    if body[:2] == b"\x1f\x8b":
        return gzip.decompress(body)
    return body


def _request(
    url: str, context: ssl.SSLContext, if_modified_since: str | None
) -> tuple[int, bytes, str | None]:
    headers = {"Accept-Encoding": "gzip", "User-Agent": "ladekoenig-collector"}
    if if_modified_since:
        headers["If-Modified-Since"] = if_modified_since
    request = urllib.request.Request(url, headers=headers, method="GET")
    last_error: Exception | None = None
    for attempt in range(1, RETRIES + 1):
        try:
            with urllib.request.urlopen(request, context=context, timeout=TIMEOUT) as resp:
                return resp.status, _decompress(resp.read()), resp.headers.get("Last-Modified")
        except urllib.error.HTTPError as err:
            if err.code in (204, 304):
                return err.code, b"", err.headers.get("Last-Modified") if err.headers else None
            if err.code < 500 and err.code != 429:
                raise FetchError(f"HTTP {err.code} für {url.split('?')[0]}") from err
            last_error = err
        except (urllib.error.URLError, TimeoutError, OSError) as err:
            last_error = err
        if attempt < RETRIES:
            time.sleep(RETRY_DELAY * attempt)
    raise FetchError(f"Abruf nach {RETRIES} Versuchen fehlgeschlagen: {last_error}")


def fetch_subscription(
    key: str,
    subscription_id: str,
    context: ssl.SSLContext,
    cache_dir: Path,
    base_url: str = BASE_URL,
) -> FetchResult:
    """Holt den neuesten Stand eines Abos; nutzt den Cache, wenn nichts Neues kommt."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    body_file = cache_dir / f"{key}.bin"
    meta_file = cache_dir / f"{key}.json"
    cached_lm: str | None = None
    if meta_file.exists() and body_file.exists():
        try:
            cached_lm = json.loads(meta_file.read_text()).get("last_modified")
        except (OSError, ValueError):
            cached_lm = None

    url = f"{base_url}?subscriptionID={subscription_id}"
    latest: bytes | None = None
    last_modified = cached_lm
    for _ in range(MAX_ROUNDS):
        status, body, lm = _request(url, context, last_modified)
        if status in (204, 304) or not body:
            break
        latest = body
        if not lm or lm == last_modified:
            last_modified = lm or last_modified
            break
        last_modified = lm

    if latest is not None:
        body_file.write_bytes(latest)
        meta_file.write_text(json.dumps({"last_modified": last_modified}))
        return FetchResult(latest, last_modified, from_cache=False)
    if body_file.exists():
        _LOGGER.info("%s: nichts Neues, nutze zwischengespeicherten Stand", key)
        return FetchResult(body_file.read_bytes(), cached_lm, from_cache=True)
    raise FetchError(f"{key}: keine Daten im Abo verfügbar")
