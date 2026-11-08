"""Nuorodų saugumas: URL kanonizavimas ir mažas išėjimo (CONNECT) tarpinis serveris su leidžiamų domenų sąrašu.

Kanoninis URL perduodamas ir httpx, ir naršyklei, todėl jie negali interpretuoti adreso skirtingai.
Tarpinis serveris (127.0.0.1, atsitiktinis prievadas, gyvas tik vieno bandymo metu) leidžia tik
CONNECT host:443, kai host praeina leidžiamų domenų patikrą IR visi jo IP viešieji; jungiamasi prie
jau patikrinto IP (DNS rebinding negalimas). Tai apima peradresavimus, iframe, iškylančius langus ir JS navigaciją.
"""
from __future__ import annotations

import hashlib
import ipaddress
import logging
import re
import selectors
import socket
import threading
import time
from urllib.parse import urlsplit

log = logging.getLogger("uno.link_safety")

MAX_URL_LEN = 2048
_LABEL_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
_BAD_CHARS_RE = re.compile(r"[\x00-\x20\x7f-￿\\]")  # kontroliniai, tarpai, ne ASCII, backslash
_EXTRA_BLOCKED = [ipaddress.ip_network(n) for n in (
    "100.64.0.0/10", "64:ff9b::/96", "64:ff9b:1::/48", "2002::/16", "2001::/32", "fec0::/10")]


def link_label(url: str) -> str:
    """Žurnalui: tik domenas ir trumpas negrįžtamas URL identifikatorius (jokių kelių / žetonų)."""
    try:
        host = urlsplit(url).hostname or "?"
    except ValueError:
        host = "?"
    return f"{host}#{hashlib.sha256(url.encode('utf-8', 'replace')).hexdigest()[:10]}"


def _valid_host(host: str) -> bool:
    labels = host.split(".")
    if len(host) > 253 or len(labels) < 2 or not all(_LABEL_RE.match(label) for label in labels):
        return False
    if not re.search(r"[a-z]", labels[-1]):  # pvz. 127.0.0.1, 2130706433, 0x7f.1
        return False
    for label in labels:
        if label.startswith("xn--"):
            try:  # punycode turi būti teisingas ir grįžtamas
                if label.encode("ascii").decode("idna").encode("idna").decode("ascii") != label:
                    return False
            except UnicodeError:
                return False
    try:
        ipaddress.ip_address(host)
        return False
    except ValueError:
        return True


def host_allowed(host: str, domains: list[str]) -> bool:
    host = host.lower()
    return _valid_host(host) and any(host == d or host.endswith("." + d) for d in domains)


def canonicalize(url: object, domains: list[str]) -> str | None:
    """Kanoninis https URL arba None. Atmeta backslash, userinfo, kontrolinius, ne-443 prievadus, IP, svetimus domenus."""
    if not isinstance(url, str) or not url or len(url) > MAX_URL_LEN or _BAD_CHARS_RE.search(url):
        return None
    try:
        parts = urlsplit(url)
        port = parts.port
        host = parts.hostname or ""
    except ValueError:
        return None
    if parts.scheme != "https" or "@" in parts.netloc or "[" in parts.netloc or port not in (None, 443):
        return None
    if parts.netloc.lower() != (host if port is None else f"{host}:{port}") or not host_allowed(host, domains):
        return None
    query = f"?{parts.query}" if parts.query else ""
    return f"https://{host}{parts.path or '/'}{query}"


def is_public_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    if not addr.is_global or addr.is_multicast or addr.is_reserved or addr.is_unspecified:
        return False
    return not any(addr in net for net in _EXTRA_BLOCKED if net.version == addr.version)


class Denied(Exception):
    """Prisijungimas atmestas (priežastis be URL kelio / žetonų)."""


def vet_target(host: str, port: int, domains: list[str], resolver=socket.getaddrinfo) -> list[str]:
    """Patikrinti CONNECT tikslą; grąžina patikrintus IP arba meta Denied."""
    if port != 443:
        raise Denied("prievadas")
    host = host.lower()
    if not host_allowed(host, domains):
        raise Denied("domenas")
    try:
        infos = resolver(host, 443, type=socket.SOCK_STREAM)
    except OSError:
        raise Denied("DNS") from None
    ips = list(dict.fromkeys(info[4][0] for info in infos))
    if not ips or not all(is_public_ip(ip) for ip in ips):
        raise Denied("neviešas IP")
    return ips


class EgressProxy:
    """Gijų CONNECT tarpinis serveris. Naudoti kaip kontekstinį valdiklį; `url` = http://127.0.0.1:<port>."""

    def __init__(self, domains: list[str], *, max_bytes: int = 40 * 1024 * 1024, resolver=socket.getaddrinfo,
                 connector=None, header_timeout: float = 10.0) -> None:
        self.domains = domains
        self.bytes_left = max_bytes
        self.exceeded = False
        self.denied: list[str] = []
        self._resolver = resolver
        self._connector = connector or (lambda ip: socket.create_connection((ip, 443), timeout=10))
        self._header_timeout = header_timeout
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._socks: set[socket.socket] = set()
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(32)
        self.url = f"http://127.0.0.1:{self._listener.getsockname()[1]}"
        threading.Thread(target=self._accept_loop, daemon=True).start()

    def __enter__(self) -> "EgressProxy":
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            socks = list(self._socks) + [self._listener]
        for sock in socks:
            try:
                sock.close()
            except OSError:
                pass

    def _track(self, sock: socket.socket) -> None:
        with self._lock:
            self._socks.add(sock)

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                client, _ = self._listener.accept()
            except OSError:
                return
            self._track(client)
            threading.Thread(target=self._handle, args=(client,), daemon=True).start()

    def _read_head(self, client: socket.socket) -> tuple[str, bytes]:
        client.settimeout(self._header_timeout)
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = client.recv(4096)
            if not chunk or len(buf) > 16384:
                raise Denied("antraštė")
            buf += chunk
        head, _, rest = buf.partition(b"\r\n\r\n")
        return head.split(b"\r\n", 1)[0].decode("latin-1"), rest

    def _handle(self, client: socket.socket) -> None:
        upstream = None
        try:
            line, rest = self._read_head(client)
            match = re.fullmatch(r"CONNECT ([A-Za-z0-9.\-]+):(\d{1,5}) HTTP/1\.[01]", line)
            if not match:
                raise Denied("ne CONNECT")  # paprastas HTTP ir viskas kita atmetama
            host, port = match.group(1), int(match.group(2))
            ips = vet_target(host, port, self.domains, self._resolver)
            upstream = self._connector(ips[0])  # jungiamasi prie jau patikrinto IP
            self._track(upstream)
            client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            if rest:
                upstream.sendall(rest)
            self._relay(client, upstream)
        except Denied as exc:
            self.denied.append(str(exc))
            log.info("Išėjimas atmestas: %s", exc)
            self._reply(client, b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
        except OSError:
            self._reply(client, b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
        finally:
            for sock in (client, upstream):
                if sock:
                    try:
                        sock.close()
                    except OSError:
                        pass

    @staticmethod
    def _reply(client: socket.socket, data: bytes) -> None:
        try:
            client.sendall(data)
        except OSError:
            pass

    def _relay(self, a: socket.socket, b: socket.socket) -> None:
        sel = selectors.DefaultSelector()
        a.setblocking(False)
        b.setblocking(False)
        sel.register(a, selectors.EVENT_READ, b)
        sel.register(b, selectors.EVENT_READ, a)
        try:
            while not self._stop.is_set():
                for key, _ in sel.select(timeout=1.0):
                    try:
                        data = key.fileobj.recv(65536)
                    except BlockingIOError:
                        continue
                    if not data:
                        return
                    with self._lock:
                        self.bytes_left -= len(data)
                        if self.bytes_left < 0:
                            self.exceeded = True
                            return
                    self._send_all(key.data, data)
        except (OSError, ValueError):
            return
        finally:
            sel.close()

    def _send_all(self, sock: socket.socket, data: bytes) -> None:
        deadline = time.monotonic() + 30
        while data:
            try:
                sent = sock.send(data)
                data = data[sent:]
            except BlockingIOError:
                if time.monotonic() > deadline or self._stop.is_set():
                    raise OSError("siuntimo riba") from None
                time.sleep(0.01)
