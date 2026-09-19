"""Identity must fail closed.

The X-Tenant-Id / X-Role dev stub used to be reachable whenever AUTH_REQUIRED
was simply absent from the environment, which made every tenant's data one
missing variable away from a browser-chosen scope. These tests pin the default
shut: no variable set means no header trust.
"""
import pytest

from app.auth import AuthError, auth_required, resolve_scope


def test_default_is_auth_required(monkeypatch):
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    assert auth_required() is True


@pytest.mark.parametrize("value", ["0", "false", "FALSE", "no"])
def test_stub_needs_an_explicit_opt_out(monkeypatch, value):
    monkeypatch.setenv("AUTH_REQUIRED", value)
    assert auth_required() is False


def test_headers_are_ignored_when_unset(monkeypatch):
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    with pytest.raises(AuthError):
        resolve_scope(x_tenant_id="00000000-0000-0000-0000-000000000000",
                      x_role="techdeva_admin", cookie=None)


def test_admin_role_cannot_be_claimed_by_header_when_unset(monkeypatch):
    monkeypatch.delenv("AUTH_REQUIRED", raising=False)
    with pytest.raises(AuthError):
        resolve_scope(x_tenant_id=None, x_role="techdeva_admin", cookie=None)
