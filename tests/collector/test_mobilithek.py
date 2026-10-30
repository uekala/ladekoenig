"""Tests für den Mobilithek-Abruf gegen einen lokalen HTTPS-Server mit Client-Zertifikat."""

from __future__ import annotations

import datetime as dt
import gzip
import ipaddress
import ssl
import threading
from email.utils import format_datetime, parsedate_to_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from ladekrone_collector.mobilithek import FetchError, build_ssl_context, fetch_subscription

T0 = dt.datetime(2026, 10, 1, 3, 0, tzinfo=dt.UTC)
PACKAGES = [
    (T0, b'{"paket": 1}'),
    (T0 + dt.timedelta(days=1), b'{"paket": 2}'),
    (T0 + dt.timedelta(days=2), b'{"paket": 3}'),
]


def _cert(subject: str, issuer_key, issuer_name, key, *, ca: bool = False, san: str | None = None):
    now = dt.datetime.now(dt.UTC)
    builder = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject)]))
        .issuer_name(issuer_name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer_key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=not ca,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=ca,
                crl_sign=ca,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
    )
    if not ca:
        builder = builder.add_extension(
            x509.ExtendedKeyUsage(
                [x509.oid.ExtendedKeyUsageOID.SERVER_AUTH, x509.oid.ExtendedKeyUsageOID.CLIENT_AUTH]
            ),
            critical=False,
        )
    if san:
        builder = builder.add_extension(
            x509.SubjectAlternativeName(
                [x509.DNSName(san), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
    return builder.sign(issuer_key, hashes.SHA256())


def _write(path: Path, cert=None, key=None) -> str:
    data = b""
    if cert is not None:
        data += cert.public_bytes(serialization.Encoding.PEM)
    if key is not None:
        data += key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    path.write_bytes(data)
    return str(path)


@pytest.fixture
def pki(tmp_path: Path) -> dict[str, str]:
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test-CA")])
    ca = _cert("Test-CA", ca_key, ca_name, ca_key, ca=True)
    server_key = ec.generate_private_key(ec.SECP256R1())
    server = _cert("localhost", ca_key, ca_name, server_key, san="localhost")
    client_key = ec.generate_private_key(ec.SECP256R1())
    client = _cert("ladekrone", ca_key, ca_name, client_key)
    return {
        "ca": _write(tmp_path / "ca.pem", ca),
        "server": _write(tmp_path / "server.pem", server, server_key),
        "client_cert": _write(tmp_path / "client.crt", client),
        "client_key": _write(tmp_path / "client.key", key=client_key),
    }


@pytest.fixture
def server(pki: dict[str, str], socket_enabled):
    requests: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:  # leise
            return

        def do_GET(self) -> None:
            requests.append(dict(self.headers))
            if "gzip" not in (self.headers.get("Accept-Encoding") or ""):
                self.send_response(406)
                self.end_headers()
                return
            since = self.headers.get("If-Modified-Since")
            # Wie die Mobilithek: ohne Header das älteste Paket, sonst das nächstneuere
            candidates = PACKAGES
            if since:
                limit = parsedate_to_datetime(since)
                candidates = [p for p in PACKAGES if p[0] > limit]
            if not candidates:
                self.send_response(304)
                self.end_headers()
                return
            stamp, body = candidates[0]
            payload = gzip.compress(body)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Last-Modified", format_datetime(stamp, usegmt=True))
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(pki["server"])
    context.load_verify_locations(pki["ca"])
    context.verify_mode = ssl.CERT_REQUIRED
    httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"https://127.0.0.1:{httpd.server_address[1]}/subscription", requests
    httpd.shutdown()


def _client_context(pki: dict[str, str]) -> ssl.SSLContext:
    context = build_ssl_context(pki["client_cert"], pki["client_key"])
    context.load_verify_locations(pki["ca"])
    return context


def test_fetches_newest_package_and_uses_cache(server, pki, tmp_path: Path) -> None:
    url, requests = server
    context = _client_context(pki)

    result = fetch_subscription("tesla", "123", context, tmp_path, base_url=url)
    assert result.body == b'{"paket": 3}'
    assert not result.from_cache
    assert len(requests) == 4  # drei Pakete + 304

    requests.clear()
    again = fetch_subscription("tesla", "123", context, tmp_path, base_url=url)
    assert again.body == b'{"paket": 3}'
    assert again.from_cache
    assert len(requests) == 1
    assert requests[0]["If-Modified-Since"]


def test_client_certificate_is_required(server, pki, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("ladekrone_collector.mobilithek.RETRY_DELAY", 0)
    url, _ = server
    context = ssl.create_default_context()
    context.load_verify_locations(pki["ca"])
    with pytest.raises(FetchError):
        fetch_subscription("tesla", "123", context, tmp_path, base_url=url)
