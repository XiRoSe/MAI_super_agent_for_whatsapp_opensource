"""Credential vault tests: encryption, isolation, JSON round-trip."""

from __future__ import annotations

from backend.credentials import (
    delete_credential,
    get_credential,
    has_credential,
    list_services,
    save_credential,
)
from backend.db.scoped import TenantScope
from backend.db.tenancy import Credential


def _scope(session, user):
    return TenantScope(session, account_id=user.account_id, user_id=user.id)


def test_save_and_get_roundtrip_encrypted(session, two_tenants):
    _, u1, _, _ = two_tenants
    s1 = _scope(session, u1)
    save_credential(s1, service="Google", data={"api_key": "secret-123", "cal": "primary"})
    # Stored blob is encrypted (plaintext not present).
    row = session.query(Credential).first()
    assert "secret-123" not in row.data_enc
    assert row.service == "google"  # normalized lower-case
    # Round trip.
    assert get_credential(s1, "google") == {"api_key": "secret-123", "cal": "primary"}


def test_isolation_between_tenants(session, two_tenants):
    _, u1, _, u2 = two_tenants
    save_credential(_scope(session, u1), service="notion", data={"token": "u1"})
    s2 = _scope(session, u2)
    assert get_credential(s2, "notion") is None
    assert has_credential(s2, "notion") is False
    assert list_services(s2) == []


def test_list_services_hides_secret(session, two_tenants):
    _, u1, _, _ = two_tenants
    s1 = _scope(session, u1)
    save_credential(s1, service="outlook", data={"token": "abc"}, label="me@work.com")
    listed = list_services(s1)
    assert listed[0]["service"] == "outlook"
    assert listed[0]["label"] == "me@work.com"
    assert "token" not in str(listed)  # no secret leaks into the listing


def test_update_and_delete(session, two_tenants):
    _, u1, _, _ = two_tenants
    s1 = _scope(session, u1)
    save_credential(s1, service="github", data={"pat": "old"})
    save_credential(s1, service="github", data={"pat": "new"})  # update, not dup
    assert session.query(Credential).count() == 1
    assert get_credential(s1, "github") == {"pat": "new"}
    assert delete_credential(s1, "github") is True
    assert get_credential(s1, "github") is None


def test_credential_tools_save_plain_and_json(session, two_tenants):
    from backend.tools.credential_tools import CredentialTools

    _, u1, _, _ = two_tenants
    tools = CredentialTools(user=u1, session=session)
    tools.save_service_credentials("stripe", "sk_test_123")  # plain key
    tools.save_service_credentials("google", '{"api_key":"k","cal":"primary"}')  # json
    s1 = _scope(session, u1)
    assert get_credential(s1, "stripe") == {"value": "sk_test_123"}
    assert get_credential(s1, "google") == {"api_key": "k", "cal": "primary"}
    assert "stripe" in tools.list_connected_services()
