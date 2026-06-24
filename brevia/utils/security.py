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
from urllib.parse import urlparse


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
