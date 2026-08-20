"""Drive knowledge base: folder bootstrap, doc CRUD round-trip, token refresh,
tenant isolation, and the not-connected tool path. The ONLY substituted piece is
the HTTP transport (gdrive._request) — vault, scope, and toolkit are real."""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

import backend.gdrive as gdrive
from backend.credentials import get_credential, save_credential
from backend.db.scoped import TenantScope
from backend.tools.docs_tools import DocsTools


def _scope(session, user):
    return TenantScope(session, account_id=user.account_id, user_id=user.id)


def _connect(session, user, **extra):
    save_credential(_scope(session, user), service=gdrive.SERVICE, data={
        "access_token": "at", "refresh_token": "rt",
        "expires_at": time.time() + 3600, **extra,
    })


class FakeDrive:
    """In-memory stand-in for the Drive v3 files API surface gdrive uses."""

    def __init__(self):
        self.files: dict[str, dict] = {}
        self.seq = 0
        self.refresh_calls = 0

    def _resp(self, payload=None, text="", status=200):
        return SimpleNamespace(status_code=status, text=text,
                               json=lambda: payload, raise_for_status=lambda: None)

    def __call__(self, method, url, **kw):
        if url == gdrive._TOKEN_URL:
            self.refresh_calls += 1
            return self._resp({"access_token": "fresh", "expires_in": 3600})
        if url.startswith(gdrive._UPLOAD_URL):
            fid = url.rsplit("/", 1)[1]
            self.files[fid]["content"] = kw["content"].decode()
            return self._resp({"id": fid})
        if url == gdrive._FILES_URL and method == "GET":
            return self._resp({"files": self._query(kw["params"]["q"])})
        if url == gdrive._FILES_URL and method == "POST":
            self.seq += 1
            fid = f"f{self.seq}"
            meta = kw["json"]
            self.files[fid] = {"id": fid, "name": meta["name"],
                               "mimeType": meta["mimeType"],
                               "parents": meta.get("parents", []), "content": ""}
            return self._resp({"id": fid})
        if url.startswith(gdrive._FILES_URL + "/"):
            fid = url.rsplit("/", 1)[1]
            if method == "DELETE":
                self.files.pop(fid, None)
                return self._resp({})
            return self._resp(text=self.files[fid]["content"])  # alt=media read
        raise AssertionError(f"unexpected call {method} {url}")

    def _query(self, q):
        out = []
        for f in self.files.values():
            if "name = " in q and f"name = '{f['name']}'" not in q:
                continue
            if "in parents" in q and not any(f"'{p}' in parents" in q for p in f["parents"]):
                continue
            if "mimeType = " in q and f"mimeType = '{f['mimeType']}'" not in q:
                continue
            out.append({"id": f["id"], "name": f["name"], "modifiedTime": "2026-07-15"})
        return out


@pytest.fixture()
def drive(monkeypatch):
    fake = FakeDrive()
    monkeypatch.setattr(gdrive, "_request", fake)
    return fake


def test_folder_bootstrap_once_and_persisted(session, two_tenants, drive):
    _, u1, *_ = two_tenants
    _connect(session, u1)
    s1 = _scope(session, u1)
    fid = gdrive.ensure_folder(s1)
    assert drive.files[fid]["mimeType"] == "application/vnd.google-apps.folder"
    # Second call reuses the persisted id — no second folder appears.
    assert gdrive.ensure_folder(s1) == fid
    assert get_credential(s1, gdrive.SERVICE)["folder_id"] == fid
    assert sum(f["mimeType"].endswith("folder") for f in drive.files.values()) == 1


def test_save_read_list_delete_roundtrip(session, two_tenants, drive):
    _, u1, *_ = two_tenants
    _connect(session, u1)
    s1 = _scope(session, u1)
    gdrive.save_doc(s1, "reading list", "# Books\n- Dune")
    assert gdrive.read_doc(s1, "reading list") == "# Books\n- Dune"
    # Overwrite updates the same file, not a duplicate.
    gdrive.save_doc(s1, "reading list", "# Books\n- Dune\n- Blindsight")
    assert [d.title for d in gdrive.list_docs(s1)] == ["reading list"]
    assert "Blindsight" in gdrive.read_doc(s1, "reading list")
    assert gdrive.delete_doc(s1, "reading list") is True
    assert gdrive.list_docs(s1) == []
    assert gdrive.read_doc(s1, "reading list") is None


def test_expired_token_refreshes_and_repersists(session, two_tenants, drive):
    _, u1, *_ = two_tenants
    _connect(session, u1, expires_at=time.time() - 10)
    s1 = _scope(session, u1)
    gdrive.ensure_folder(s1)
    assert drive.refresh_calls == 1
    stored = get_credential(s1, gdrive.SERVICE)
    assert stored["access_token"] == "fresh" and stored["expires_at"] > time.time()


def test_tenant_isolation_unconnected_user_gets_nothing(session, two_tenants, drive):
    _, u1, _, u2 = two_tenants
    _connect(session, u1)
    gdrive.save_doc(_scope(session, u1), "secrets", "u1 only")
    with pytest.raises(gdrive.DriveNotConnected):
        gdrive.read_doc(_scope(session, u2), "secrets")


def test_tools_surface_connect_link_when_not_connected(session, two_tenants, monkeypatch):
    from backend.config import settings

    _, u1, *_ = two_tenants
    monkeypatch.setattr(settings, "google_client_id", "cid")
    monkeypatch.setattr(settings, "google_client_secret", "cs")
    out = DocsTools(user=u1, session=session).save_document("x", "y")
    assert "/oauth/google/start?s=" in out

    monkeypatch.setattr(settings, "google_client_id", "")
    out = DocsTools(user=u1, session=session).list_documents()
    assert "not configured" in out


def test_oauth_state_roundtrip_and_tamper_rejected(two_tenants):
    from backend import oauth_google

    _, u1, *_ = two_tenants
    state = oauth_google._encode_state(u1)
    claims = oauth_google._decode_state(state)
    assert claims["acct"] == u1.account_id and claims["uid"] == u1.id
    assert oauth_google._decode_state(state + "x") is None
