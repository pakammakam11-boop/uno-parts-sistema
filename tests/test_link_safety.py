"""Išėjimo tarpinis serveris: sprendimai (su netikru DNS) ir CONNECT elgsena per vietinius lizdus."""
import socket
import threading
import time

import pytest

from core import link_safety
from core.link_safety import Denied, EgressProxy, is_public_ip, vet_target

DOMAINS = ["agnum.lt"]
PUBLIC = "93.184.216.34"


def resolver_for(mapping):
    def resolve(host, port, type=0):  # noqa: A002
        if host not in mapping:
            raise socket.gaierror("nėra")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in mapping[host]]
    return resolve


@pytest.mark.parametrize("ip", [
    "127.0.0.1", "10.1.2.3", "172.16.0.5", "192.168.1.1", "169.254.169.254", "100.64.0.1", "100.127.255.254",
    "0.0.0.0", "224.0.0.1", "240.0.0.1", "255.255.255.255", "::1", "fe80::1", "fc00::1", "ff02::1",
    "::ffff:127.0.0.1", "::ffff:10.0.0.1", "64:ff9b::7f00:1", "2002:7f00:1::", "not-an-ip", "192.0.0.1", "198.18.0.1",
])
def test_non_public_ips_rejected(ip):
    assert not is_public_ip(ip)


@pytest.mark.parametrize("ip", [PUBLIC, "8.8.8.8", "1.1.1.1", "2606:4700:4700::1111"])
def test_public_ips_accepted(ip):
    assert is_public_ip(ip)


def test_vet_target_decisions():
    ok = resolver_for({"mail1.agnum.lt": [PUBLIC]})
    assert vet_target("mail1.agnum.lt", 443, DOMAINS, ok) == [PUBLIC]
    assert vet_target("MAIL1.AGNUM.LT", 443, DOMAINS, ok) == [PUBLIC]
    with pytest.raises(Denied):
        vet_target("mail1.agnum.lt", 80, DOMAINS, ok)
    with pytest.raises(Denied):
        vet_target("evil.com", 443, DOMAINS, resolver_for({"evil.com": [PUBLIC]}))
    with pytest.raises(Denied):
        vet_target("127.0.0.1", 443, DOMAINS, ok)
    with pytest.raises(Denied):
        vet_target("a.agnum.lt", 443, DOMAINS, resolver_for({"a.agnum.lt": ["10.0.0.5"]}))
    with pytest.raises(Denied):
        vet_target("a.agnum.lt", 443, DOMAINS, resolver_for({"a.agnum.lt": [PUBLIC, "169.254.169.254"]}))
    with pytest.raises(Denied):
        vet_target("nera.agnum.lt", 443, DOMAINS, ok)


def _port(proxy: EgressProxy) -> int:
    return int(proxy.url.rsplit(":", 1)[1])


def _connect_via(proxy: EgressProxy, target: str) -> bytes:
    with socket.create_connection(("127.0.0.1", _port(proxy)), timeout=5) as s:
        s.sendall(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n".encode())
        return s.recv(4096)


def test_proxy_connect_policy_and_vetted_ip():
    seen_ips = []
    echo = socket.socket()
    echo.bind(("127.0.0.1", 0))
    echo.listen(1)

    def serve():
        conn, _ = echo.accept()
        conn.sendall(conn.recv(100).upper())
        conn.close()
    threading.Thread(target=serve, daemon=True).start()

    def connector(ip):
        seen_ips.append(ip)
        return socket.create_connection(("127.0.0.1", echo.getsockname()[1]), timeout=5)

    resolver = resolver_for({"mail1.agnum.lt": [PUBLIC], "evil.com": [PUBLIC], "int.agnum.lt": ["10.0.0.5"]})
    with EgressProxy(DOMAINS, resolver=resolver, connector=connector) as proxy:
        for bad in ("evil.com:443", "mail1.agnum.lt:8443", "int.agnum.lt:443", "169.254.169.254:443", "[::1]:443"):
            assert _connect_via(proxy, bad).startswith(b"HTTP/1.1 403"), bad
        with socket.create_connection(("127.0.0.1", _port(proxy)), timeout=5) as s:  # paprastas HTTP
            s.sendall(b"GET http://mail1.agnum.lt/ HTTP/1.1\r\nHost: mail1.agnum.lt\r\n\r\n")
            assert s.recv(100).startswith(b"HTTP/1.1 403")
        assert seen_ips == []  # joks neleistinas tikslas nesukėlė jungimosi
        with socket.create_connection(("127.0.0.1", _port(proxy)), timeout=5) as s:
            s.sendall(b"CONNECT mail1.agnum.lt:443 HTTP/1.1\r\n\r\n")
            assert s.recv(100).startswith(b"HTTP/1.1 200")
            s.sendall(b"hello")
            assert s.recv(100) == b"HELLO"
        assert seen_ips == [PUBLIC]  # jungtasi būtent prie patikrinto IP
    echo.close()


def test_proxy_byte_budget_cuts_tunnel():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)

    def serve():
        conn, _ = srv.accept()
        try:
            for _ in range(200):
                conn.sendall(b"x" * 1000)
        except OSError:
            pass
        conn.close()
    threading.Thread(target=serve, daemon=True).start()
    resolver = resolver_for({"mail1.agnum.lt": [PUBLIC]})
    with EgressProxy(DOMAINS, max_bytes=5000, resolver=resolver,
                     connector=lambda ip: socket.create_connection(("127.0.0.1", srv.getsockname()[1]))) as proxy:
        with socket.create_connection(("127.0.0.1", _port(proxy)), timeout=5) as s:
            s.sendall(b"CONNECT mail1.agnum.lt:443 HTTP/1.1\r\n\r\n")
            total, deadline = 0, time.monotonic() + 5
            s.settimeout(5)
            try:
                while time.monotonic() < deadline:
                    chunk = s.recv(65536)
                    if not chunk:
                        break
                    total += len(chunk)
            except OSError:
                pass
        assert total < 20000 and proxy.exceeded
    srv.close()


def test_redirect_to_non_allowlisted_host_fails_through_proxy(tmp_path):
    """Integracija: httpx per tarpinį serverį; leidžiamas domenas nukreipia į svetimą -> jokio antro ryšio."""
    import datetime
    import ssl

    pytest.importorskip("cryptography")
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    import httpx

    from core import link_invoices

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "mail1.agnum.lt")])
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(1).not_valid_before(datetime.datetime(2020, 1, 1)).not_valid_after(datetime.datetime(2040, 1, 1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("mail1.agnum.lt")]), False)
            .sign(key, hashes.SHA256()))
    (tmp_path / "c.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (tmp_path / "k.pem").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                                       serialization.NoEncryption()))
    sctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    sctx.load_cert_chain(tmp_path / "c.pem", tmp_path / "k.pem")
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    hits = []

    def serve():
        while True:
            try:
                raw, _ = srv.accept()
                tls = sctx.wrap_socket(raw, server_side=True)
                hits.append(tls.recv(4096)[:3])
                tls.sendall(b"HTTP/1.1 302 Found\r\nLocation: https://evil.example.com/steal\r\nContent-Length: 0\r\n\r\n")
                tls.close()
            except (OSError, ssl.SSLError):
                return
    threading.Thread(target=serve, daemon=True).start()
    resolver = resolver_for({"mail1.agnum.lt": [PUBLIC], "evil.example.com": [PUBLIC]})
    real_client = httpx.Client
    httpx.Client = lambda **kw: real_client(verify=False, **kw)  # savasis sertifikatas tik šiame teste
    try:
        # Tarpinis serveris leidžia ir example.com, kad įrodytume: kanonizacija atmeta peradresavimą dar prieš ryšį.
        with EgressProxy(["agnum.lt"], resolver=resolver,
                         connector=lambda ip: socket.create_connection(("127.0.0.1", srv.getsockname()[1]))) as proxy:
            result = link_invoices._fetch_http("https://mail1.agnum.lt/x", DOMAINS, time.monotonic() + 20, proxy.url)
            assert result is None and hits == [b"GET"]
            assert _connect_via(proxy, "evil.example.com:443").startswith(b"HTTP/1.1 403")  # ir pats tarpinis jį atmeta
            # naršyklės tipo klientas, automatiškai sekantis peradresavimus: tarpinis serveris užkerta kelią
            hits.clear()
            with real_client(proxy=proxy.url, verify=False, follow_redirects=True) as follower:
                with pytest.raises(httpx.ProxyError):
                    follower.get("https://mail1.agnum.lt/x")
            assert hits == [b"GET"]  # tik pirmas hop pasiektas, svetimas domenas niekada
    finally:
        httpx.Client = real_client
        srv.close()
