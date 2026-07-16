"""Security helpers (ROADMAP §12).

Two threats are guarded here:

* **Zip-slip (archive-internal)** — EPUBs are read fully in-memory (``ZipFile.read``),
  never extracted to disk, so no filesystem extraction guard is needed. The live guard is
  ``resolve_archive_href``: an XHTML href like ``../../secret`` must not escape the
  archive root when resolving manifest paths.
* **SSRF / key leakage** — with an arbitrary ``--api-endpoint``, we must never forward a
  provider's API key to an unexpected host. ``assert_endpoint_allowed`` enforces that the
  call target is the provider's canonical host or a local/private one
  (``is_local_host``). Wired in ``breviabook.llm.factory``.
"""

from __future__ import annotations

import ipaddress
import posixpath
from urllib.parse import unquote, urlparse


def is_local_host(host: str) -> bool:
    """Return True if ``host`` is provably local.

    Local means a loopback / private / link-local / reserved IP literal, or a
    ``localhost`` / ``*.localhost`` / ``.local`` hostname. Bare single-label hostnames
    (e.g. ``gpubox``) resolve via search-domain and cannot be proven private, so they
    are NOT considered local.
    """
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return host == "localhost" or host.endswith((".localhost", ".local"))
    return ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved


def resolve_archive_href(base_href: str, rel_href: str) -> str:
    """Resolve ``rel_href`` against ``base_href`` *inside a ZIP/EPUB*, refusing traversal.

    Works on archive-internal POSIX paths (no filesystem access). ``base_href`` is the path
    of the referencing file within the archive (e.g. ``OEBPS/text/ch1.xhtml``).

    Raises:
        ValueError: if the resolved path escapes the archive root (zip-slip).
    """
    rel = unquote(rel_href.split("#", 1)[0].split("?", 1)[0])
    base_dir = posixpath.dirname(base_href)
    resolved = posixpath.normpath(posixpath.join(base_dir, rel))
    if resolved.startswith("../") or resolved.startswith("/"):
        raise ValueError(f"Unsafe archive href (zip-slip): {rel_href!r}")
    return resolved


def assert_endpoint_allowed(endpoint: str, allowed_hosts: set[str]) -> None:
    """Ensure ``endpoint`` points at an allowed host before attaching credentials.

    Raises:
        ValueError: if the endpoint host is not in ``allowed_hosts``.
    """
    host = urlparse(endpoint).hostname or ""
    if host not in allowed_hosts:
        raise ValueError(
            f"Refusing to send credentials to unlisted host {host!r}; "
            f"allowed: {sorted(allowed_hosts)}. "
            "If this is your own server, use its private IP or a .local name, "
            "or unset the provider API key to connect without credentials."
        )
