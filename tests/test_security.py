"""Unit tests for the SSRF / key-leakage guard and the local-host policy."""

from __future__ import annotations

import pytest

from breviabook.utils.security import assert_endpoint_allowed, is_local_host


@pytest.mark.parametrize(
    "host",
    [
        "localhost",
        "foo.localhost",
        "nas.local",
        "127.0.0.1",
        "127.5.6.7",
        "::1",
        "10.0.0.5",
        "172.16.0.10",
        "172.31.255.255",
        "192.168.1.50",
        "169.254.1.1",  # link-local
    ],
)
def test_is_local_host_accepts_provably_local(host: str) -> None:
    assert is_local_host(host) is True


@pytest.mark.parametrize(
    "host",
    [
        "attacker.example",
        "gpubox",  # bare single-label hostname: resolves via search-domain, not provably private
        "localhost.evil.com",  # suffix trick
        "api.openai.com",
        "example.com",
        "8.8.8.8",
        "1.1.1.1",
        "172.32.0.1",  # just outside 172.16/12
    ],
)
def test_is_local_host_rejects_public_or_ambiguous(host: str) -> None:
    assert is_local_host(host) is False


def test_assert_endpoint_allowed_passes_for_allowed_host() -> None:
    assert_endpoint_allowed("https://api.openai.com/v1", {"api.openai.com"})


def test_assert_endpoint_allowed_refuses_with_remediation() -> None:
    with pytest.raises(ValueError, match="attacker.example") as exc_info:
        assert_endpoint_allowed("https://attacker.example/v1", {"api.openai.com"})
    msg = str(exc_info.value)
    assert "private IP" in msg
    assert ".local" in msg
    assert "unset the provider API key" in msg
