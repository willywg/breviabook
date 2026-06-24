"""Security helpers (ROADMAP §12).

Two threats are guarded here:

* **Zip-slip** — when unpacking an EPUB (a ZIP), an entry path like ``../../etc/x`` must
  not escape the extraction root. ``safe_extract_path`` validates each entry. Used from
  Phase 1's EPUB parser.
* **SSRF / key leakage** — with an arbitrary ``--api-endpoint``, we must never forward a
  provider's API key to an unexpected host. ``assert_endpoint_allowed`` enforces that the
  call target matches the configured provider host. Fully wired in Phase 9; defined now so
  the contract exists from the start.
"""

from __future__ import annotations

import os
import posixpath
from urllib.parse import unquote, urlparse


def safe_extract_path(base_dir: str, entry_name: str) -> str:
    """Resolve ``entry_name`` under ``base_dir``, refusing any path that escapes it.

    Raises:
        ValueError: if the resolved path would land outside ``base_dir`` (zip-slip).
    """
    base = os.path.realpath(base_dir)
    target = os.path.realpath(os.path.join(base, entry_name))
    if target != base and not target.startswith(base + os.sep):
        raise ValueError(f"Unsafe archive entry path (zip-slip): {entry_name!r}")
    return target


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
            f"allowed: {sorted(allowed_hosts)}"
        )
