"""Outbound HTTP that a stranger is allowed to aim.

WHY THIS MODULE EXISTS
POST /signup is public and unauthenticated - it is the front door on
techdeva.net. It accepts website_url and competitors[].website, and the Analysis
agent then fetches them server-side, from inside the Docker network, following
redirects.

Without a check on WHERE those requests go, a stranger can point the platform at
    http://169.254.169.254/          cloud instance metadata
    http://db:5432 / http://redis:6379 / http://api:8000
    http://127.0.0.1:4000            the LiteLLM gateway, which has no auth
    http://10.x / 192.168.x          anything else on the host's networks
and read the response back out of their own trial dashboard, because the fetch
result is written into the analysis report they are shown. That is a server-side
request forgery reachable with no credential at all.

WHAT THIS DOES
get() is a drop-in replacement for requests.get that:
  1. requires http/https - no file://, ftp://, gopher://, data://
  2. resolves the hostname and refuses private, loopback, link-local, multicast,
     reserved and unspecified addresses, for both IPv4 and IPv6
  3. refuses non-web ports, so a public host cannot be used to reach an internal
     service by port number
  4. follows redirects MANUALLY, re-running every check on each hop - an allowed
     public host that 302s to 169.254.169.254 is the classic bypass, and
     allow_redirects=True walks straight into it
  5. caps the response body, so a huge file cannot exhaust the worker

WHAT IT DELIBERATELY DOES NOT DO
It does not pin the resolved address into the connection, so a DNS-rebinding
race remains: the attacker must flip DNS between our resolution and the socket
connect. Closing that properly needs a custom connection adapter. Stated plainly
rather than left implied.

ALLOW_PRIVATE_FETCH=true disables every address check. It exists so a developer
can analyse a site on their own machine. It must never be set on the VPS.
"""
from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urlparse, urlunparse

import requests

# Ports a real website is served on. Anything else is a service, not a site.
ALLOWED_PORTS = {80, 443, 8080, 8443}
MAX_REDIRECTS = 4
MAX_BYTES = int(os.environ.get("FETCH_MAX_BYTES", str(5 * 1024 * 1024)))
DEFAULT_TIMEOUT = float(os.environ.get("ANALYSIS_HTTP_TIMEOUT", "12"))


class BlockedURL(Exception):
    """The URL is not one this server is permitted to fetch.

    The message is safe to show a user: it names which rule was hit and never
    reveals internal hostnames or addresses that happened to resolve.
    """


def _dev_bypass() -> bool:
    return os.environ.get("ALLOW_PRIVATE_FETCH", "").strip().lower() in {"1", "true", "yes"}


def _address_is_public(ip_str: str) -> bool:
    """True only for addresses that belong to the public internet."""
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
            or ip.is_reserved or ip.is_unspecified):
        return False
    # IPv6 wrappers around IPv4 (::ffff:169.254.169.254) must be unwrapped, or a
    # link-local IPv4 address sails through as a "global" IPv6 one.
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        return _address_is_public(str(mapped))
    sixto4 = getattr(ip, "sixtofour", None)
    if sixto4 is not None:
        return _address_is_public(str(sixto4))
    return True


def assert_fetchable(url: str) -> str:
    """Raise BlockedURL unless this exact URL is safe to request."""
    if not url or not isinstance(url, str):
        raise BlockedURL("no URL supplied")
    parts = urlparse(url.strip())
    if parts.scheme.lower() not in ("http", "https"):
        raise BlockedURL("only http and https URLs can be fetched")
    host = parts.hostname
    if not host:
        raise BlockedURL("the URL has no hostname")
    port = parts.port or (443 if parts.scheme.lower() == "https" else 80)
    if port not in ALLOWED_PORTS:
        raise BlockedURL(f"port {port} is not a web port")
    if _dev_bypass():
        return url
    # A literal address, or a name - either way check EVERY address it resolves
    # to. A hostname with one public and one private A record must be refused.
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise BlockedURL("that hostname does not resolve")
    if not infos:
        raise BlockedURL("that hostname does not resolve")
    for info in infos:
        if not _address_is_public(info[4][0]):
            # Never echo the address back: on an internal-name probe the error
            # itself would confirm what is running where.
            raise BlockedURL("that address is not on the public internet")
    return url


def get(url: str, *, headers: dict | None = None, timeout: float | None = None,
        max_bytes: int = MAX_BYTES) -> requests.Response:
    """Fetch a public URL, checking every redirect hop."""
    timeout = timeout or DEFAULT_TIMEOUT
    current = url.strip()
    for _hop in range(MAX_REDIRECTS + 1):
        assert_fetchable(current)
        resp = requests.get(current, headers=headers or {}, timeout=timeout,
                            allow_redirects=False, stream=True)
        if resp.is_redirect or resp.status_code in (301, 302, 303, 307, 308):
            location = resp.headers.get("location", "")
            resp.close()
            if not location:
                raise BlockedURL("redirect with no destination")
            # A relative Location must resolve against the CURRENT url, or a
            # path-only redirect silently escapes the check.
            current = requests.compat.urljoin(current, location)
            continue
        body = b""
        for chunk in resp.iter_content(8192):
            body += chunk
            if len(body) > max_bytes:
                break
        resp._content = body[:max_bytes]
        resp._content_consumed = True
        resp.url = current
        return resp
    raise BlockedURL("too many redirects")


def normalise(url: str | None) -> str | None:
    """Add https:// to a bare hostname, or return None. No fetching, no checks."""
    if not url or not str(url).strip():
        return None
    u = str(url).strip()
    if not urlparse(u).scheme:
        u = "https://" + u
    p = urlparse(u)
    if not p.hostname:
        return None
    return urlunparse(p)
