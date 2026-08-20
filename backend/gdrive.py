"""Google Drive knowledge base — per-user markdown documents in a Drive folder.

Mai's KB lives in the USER'S OWN Drive, in a folder this app creates. The OAuth
grant uses the `drive.file` scope, so the token can only ever see files this app
created — the folder boundary is enforced by Google, not by prompt discipline.

Credentials come from the tenant vault (service key "google_drive"); the blob
holds the OAuth token set plus the bootstrap folder id:
    {access_token, refresh_token, expires_at, folder_id}
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

from backend.config import settings
from backend.credentials import get_credential, save_credential
from backend.db.scoped import TenantScope
from backend.logging_config import get_logger

logger = get_logger(__name__)

SERVICE = "google_drive"
SCOPE = "https://www.googleapis.com/auth/drive.file"
FOLDER_NAME = "Mai"
_FOLDER_MIME = "application/vnd.google-apps.folder"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_FILES_URL = "https://www.googleapis.com/drive/v3/files"
_UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files"


class DriveNotConnected(RuntimeError):
    """No usable google_drive credential in the vault for this user."""


@dataclass(frozen=True)
class DocMeta:
    id: str
    title: str
    modified: str


def _request(method: str, url: str, **kwargs) -> httpx.Response:
    """The single HTTP seam — tests substitute this, everything above it is real."""
    return httpx.request(method, url, timeout=20, **kwargs)


def _creds(scope: TenantScope) -> dict:
    data = get_credential(scope, SERVICE)
    if not data or not data.get("refresh_token"):
        raise DriveNotConnected("google_drive is not connected for this user")
    return data


def _access_token(scope: TenantScope) -> str:
    """Return a live access token, refreshing (and re-persisting) if expired."""
    data = _creds(scope)
    if data.get("access_token") and time.time() < float(data.get("expires_at", 0)) - 60:
        return data["access_token"]
    resp = _request("POST", _TOKEN_URL, data={
        "client_id": settings.google_client_id,
        "client_secret": settings.google_client_secret,
        "refresh_token": data["refresh_token"],
        "grant_type": "refresh_token",
    })
    if resp.status_code != 200:
        logger.warning("gdrive.refresh_failed", extra={"ctx_status": resp.status_code})
        raise DriveNotConnected("google_drive token refresh failed — reconnect needed")
    tok = resp.json()
    data["access_token"] = tok["access_token"]
    data["expires_at"] = time.time() + int(tok.get("expires_in", 3600))
    save_credential(scope, service=SERVICE, data=data)
    return data["access_token"]


def _headers(scope: TenantScope) -> dict:
    return {"Authorization": f"Bearer {_access_token(scope)}"}


def _api(scope: TenantScope, method: str, url: str, **kwargs) -> httpx.Response:
    resp = _request(method, url, headers=_headers(scope), **kwargs)
    if resp.status_code == 401:
        raise DriveNotConnected("google_drive token rejected — reconnect needed")
    resp.raise_for_status()
    return resp


def ensure_folder(scope: TenantScope) -> str:
    """Return the KB folder id, creating the folder (and persisting the id) once."""
    data = _creds(scope)
    if data.get("folder_id"):
        return data["folder_id"]
    q = f"name = '{FOLDER_NAME}' and mimeType = '{_FOLDER_MIME}' and trashed = false"
    found = _api(scope, "GET", _FILES_URL, params={"q": q, "fields": "files(id)"}).json()
    if found.get("files"):
        folder_id = found["files"][0]["id"]
    else:
        created = _api(scope, "POST", _FILES_URL,
                       json={"name": FOLDER_NAME, "mimeType": _FOLDER_MIME})
        folder_id = created.json()["id"]
        logger.info("gdrive.folder_created", extra={"ctx_folder": folder_id})
    data = _creds(scope)  # re-read: _api may have refreshed+saved the token blob
    data["folder_id"] = folder_id
    save_credential(scope, service=SERVICE, data=data)
    return folder_id


def _filename(title: str) -> str:
    title = title.strip()
    return title if title.endswith(".md") else f"{title}.md"


def _find_doc(scope: TenantScope, folder_id: str, title: str) -> str | None:
    name = _filename(title).replace("'", "\\'")
    q = f"name = '{name}' and '{folder_id}' in parents and trashed = false"
    files = _api(scope, "GET", _FILES_URL,
                 params={"q": q, "fields": "files(id)"}).json().get("files", [])
    return files[0]["id"] if files else None


def list_docs(scope: TenantScope) -> list[DocMeta]:
    folder_id = ensure_folder(scope)
    q = f"'{folder_id}' in parents and trashed = false"
    files = _api(scope, "GET", _FILES_URL, params={
        "q": q, "fields": "files(id,name,modifiedTime)", "orderBy": "name",
    }).json().get("files", [])
    return [DocMeta(id=f["id"], title=f["name"].removesuffix(".md"),
                    modified=f.get("modifiedTime", "")) for f in files]


def read_doc(scope: TenantScope, title: str) -> str | None:
    file_id = _find_doc(scope, ensure_folder(scope), title)
    if file_id is None:
        return None
    return _api(scope, "GET", f"{_FILES_URL}/{file_id}", params={"alt": "media"}).text


def save_doc(scope: TenantScope, title: str, body: str) -> DocMeta:
    """Create or overwrite a document. Metadata first, then content upload."""
    folder_id = ensure_folder(scope)
    file_id = _find_doc(scope, folder_id, title)
    if file_id is None:
        created = _api(scope, "POST", _FILES_URL, json={
            "name": _filename(title), "parents": [folder_id], "mimeType": "text/markdown",
        }).json()
        file_id = created["id"]
    _api(scope, "PATCH", f"{_UPLOAD_URL}/{file_id}", params={"uploadType": "media"},
         content=body.encode("utf-8"))
    logger.info("gdrive.doc_saved", extra={"ctx_doc": _filename(title)})
    return DocMeta(id=file_id, title=title.strip().removesuffix(".md"), modified="")


def delete_doc(scope: TenantScope, title: str) -> bool:
    file_id = _find_doc(scope, ensure_folder(scope), title)
    if file_id is None:
        return False
    _api(scope, "DELETE", f"{_FILES_URL}/{file_id}")
    logger.info("gdrive.doc_deleted", extra={"ctx_doc": _filename(title)})
    return True
