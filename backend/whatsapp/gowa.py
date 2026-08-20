"""Unofficial WhatsApp provider — GOWA multi-device fork.

Matches the `go-whatsapp-web-multidevice` fork used in upsailor-vision, which adds
**multi-device** routing: every call carries a `device_id` query param, so ONE
GOWA instance hosts many numbers (one device per tenant). Mai's per-account
`session_id` IS the GOWA `device_id`.

Unofficial => QR login, no Meta, no fees, no 24h window / templates.
Endpoints (from the upsailor integration):
  POST   /devices                         {"device_id": "..."}        create device
  GET    /app/login?device_id=...         -> {"results": {"qr_link": ...}}
  GET    /app/logout?device_id=...
  GET    /app/reconnect?device_id=...
  GET    /devices?device_id=...           device/connection status
  POST   /send/message?device_id=...      {"phone": "972…", "message": "…"}
Inbound webhook payload:
  {"event": "message", "device_id": "...",
   "payload": {"id": "...", "from": "972…@s.whatsapp.net", "from_name": "...", "body": "..."}}
"""

from __future__ import annotations

import hashlib
import hmac
import time

import httpx

from backend.config import settings
from backend.crypto import sealing
from backend.crypto.keyservice import get_key_service
from backend.db.base import SessionLocal
from backend.logging_config import get_logger
from backend.whatsapp.provider import InboundMessage, WhatsAppProvider
from backend.whatsapp.templates import Template

logger = get_logger(__name__)

# -- Chat-list TTL cache -------------------------------------------------------
# Two separate dicts because the return shapes differ:
#   _LIST_CHATS_CACHE  — list_chats  (no `ts` field)
#   _RECENT_CHATS_CACHE — recent_chats (includes `ts` field)
# Both map  key -> (fetched_monotonic: float, result_list: list[dict])
_LIST_CHATS_CACHE: dict[str, tuple[float, list[dict]]] = {}
_RECENT_CHATS_CACHE: dict[str, tuple[float, list[dict]]] = {}
_CONTACTS_CACHE: dict[str, tuple[float, dict[str, str]]] = {}
_CHATS_CACHE_TTL: float = 90.0   # seconds until a cached result is considered stale
_CHATS_TIMEOUT: float = 12.0     # short per-call timeout for /chats (vs the 30s default)
_CONTACTS_TIMEOUT: float = 12.0  # same short budget for /user/my/contacts


def _decrypt_inbound(epoch_id: int | None, ciphertext_b64: str | None) -> str:
    """Decrypt a GOWA-sealed body for THIS turn. Fail-soft: a shredded/missing key
    or a corrupt blob yields '' (an unreadable message), never an exception — the
    ciphertext is still stored so a later read can retry within the 4-day window."""
    if epoch_id is None or not ciphertext_b64:
        return ""
    try:
        priv = get_key_service().private_for(int(epoch_id))
        if priv is None:
            return ""
        return sealing.open_sealed(ciphertext_b64, priv)
    except Exception as exc:
        logger.warning("gowa.inbound_decrypt_failed",
                       extra={"ctx_epoch": epoch_id, "ctx_error": str(exc)})
        return ""


def _decrypt_chat_name(raw: str | None, jid: str = "") -> str:
    """Decrypt a GOWA chat name read from the `/chats` list.

    The forked GOWA seals each stored chat name to Mai's epoch public key using
    the SAME sealed-box mechanism as message bodies, on the wire as
    `NSEAL:<epoch>:<ctB64>`. The `NSEAL:` sentinel lets us tell sealed names from
    legacy/plaintext ones unambiguously:
      * starts with `NSEAL:` -> parse + decrypt with that epoch's private key; a
        shredded/missing epoch yields '[expired]'.
      * otherwise -> legacy/empty, returned as-is.
    FAIL-SOFT: any parse/decrypt error falls back to the JID-derived label (or
    ''). This powers find_chats/read_chat/access and must NEVER raise."""
    raw = raw or ""
    if not raw.startswith("NSEAL:"):
        return raw
    try:
        _, epoch_str, ct_b64 = raw.split(":", 2)
        priv = get_key_service().private_for(int(epoch_str))
        if priv is None:
            return "[expired]"
        return sealing.open_sealed(ct_b64, priv)
    except Exception as exc:
        logger.warning("gowa.chat_name_decrypt_failed", extra={"ctx_error": str(exc)})
        return _jid_to_number(jid) if jid else ""


class GowaWhatsAppProvider(WhatsAppProvider):
    unofficial = True

    def __init__(self) -> None:
        self.base_url = settings.whatsapp_gateway_url.rstrip("/")
        self.user = settings.whatsapp_gateway_user
        self.password = settings.whatsapp_gateway_password
        self.api_key = settings.whatsapp_gateway_api_key
        self.webhook_secret = settings.whatsapp_gateway_webhook_secret

    # -- HTTP plumbing ------------------------------------------------------
    def _auth(self):
        return (self.user, self.password) if self.user else None

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["X-Api-Key"] = self.api_key
        return h

    def _request(self, method: str, path: str, *, device_id: str | None = None,
                 timeout: float = 30, **kw):
        if not self.base_url:
            logger.info("gowa.no_gateway", extra={"ctx_path": path})
            return None
        params = dict(kw.pop("params", {}) or {})
        if device_id:
            params["device_id"] = device_id
        try:
            resp = httpx.request(
                method, f"{self.base_url}{path}", auth=self._auth(),
                headers=self._headers(), params=params, timeout=timeout, **kw
            )
            if resp.status_code >= 400:
                logger.warning("gowa.http_error", extra={"ctx_status": resp.status_code,
                               "ctx_path": path, "ctx_body": resp.text[:200]})
                return None
            return resp
        except httpx.HTTPError as exc:
            logger.warning("gowa.http_exc", extra={"ctx_error": str(exc), "ctx_path": path})
            return None

    def _media_public_url(self, value) -> str | None:
        """Build a fetchable PUBLIC url for inbound media, or None if unusable.

        With gateway auto-download ON, media arrives as a local path string like
        'statics/media/...ogg' (or a dict carrying 'path'/'url'); that path is served
        decrypted at {base}/{path}, which the agent can fetch directly. An absolute
        http(s) url is returned as-is when it's already fetchable (e.g. a gateway
        statics url). Only an ENCRYPTED WhatsApp CDN reference — 'mmg.whatsapp.net'
        or a '.enc' url — carries no usable bytes -> return None.
        """
        if isinstance(value, dict):
            value = value.get("path") or value.get("url") or ""
        path = str(value or "").strip()
        if not path:
            return None
        if path.startswith("http://") or path.startswith("https://"):
            if "mmg.whatsapp.net" in path or path.endswith(".enc"):
                return None  # encrypted WhatsApp CDN ref -> not fetchable
            return path  # already an absolute, fetchable url
        return f"{self.base_url}/{path.lstrip('/')}"

    # -- device/session lifecycle ------------------------------------------
    def create_session(self, session_id: str) -> None:
        """Register a device (number slot) on the gateway."""
        self._request("POST", "/devices", json={"device_id": session_id})

    def get_login(self, session_id: str) -> dict | None:
        """Fetch a FRESH QR for the device. Returns {'qr','qr_duration'} or None."""
        resp = self._request("GET", "/app/login", device_id=session_id)
        if resp is None:
            return None
        try:
            results = resp.json().get("results", {})
            qr = results.get("qr_link") or results.get("qr") or results.get("qrcode")
            # GOWA returns an http:// image URL; upgrade so the https dashboard
            # can render it without a mixed-content block.
            if isinstance(qr, str) and qr.startswith("http://"):
                qr = "https://" + qr[len("http://"):]
            return {"qr": qr, "qr_duration": int(results.get("qr_duration") or 30)}
        except Exception:
            return None

    def list_chats(self, device_id: str, *, limit: int = 50) -> list[dict]:
        """List the account's chats/groups: [{jid, name, is_group}].

        Results are cached for _CHATS_CACHE_TTL seconds.  On a GOWA timeout or
        error the last cached result (even if stale) is returned rather than
        raising, so a slow gateway never blocks an agent turn for 30 s.
        """
        limit = min(max(1, limit), 100)  # GOWA rejects limit > 100
        cache_key = device_id or ""
        cached = _LIST_CHATS_CACHE.get(cache_key)
        if cached is not None and (time.monotonic() - cached[0]) < _CHATS_CACHE_TTL:
            return cached[1]

        resp = self._request("GET", "/chats", device_id=device_id,
                             params={"limit": limit}, timeout=_CHATS_TIMEOUT)
        if resp is None:
            stale = _LIST_CHATS_CACHE.get(cache_key)
            return stale[1] if stale is not None else []
        try:
            results = resp.json().get("results", {})
            data = results.get("data") if isinstance(results, dict) else results
            contacts_map = self.live_contacts(device_id)
            out = []
            for c in data or []:
                jid = c.get("jid") or c.get("chat_jid") or c.get("id") or ""
                if not jid:
                    continue
                # Live contact name takes priority; GOWA name decryption is the legacy
                # path (and will be empty once GOWA stops persisting names for privacy).
                name = (contacts_map.get(str(jid))
                        or _decrypt_chat_name(c.get("name"), str(jid)) or c.get("subject")
                        or c.get("pushName") or c.get("push_name") or _jid_to_number(str(jid)))
                out.append({"jid": str(jid), "name": name, "is_group": "@g.us" in str(jid)})
            _LIST_CHATS_CACHE[cache_key] = (time.monotonic(), out)
            return out
        except Exception as exc:
            logger.warning("gowa.chats_failed", extra={"ctx_error": str(exc)})
            stale = _LIST_CHATS_CACHE.get(cache_key)
            return stale[1] if stale is not None else []

    def recent_chats(self, *, session_id: str | None = None, days: int = 7,
                     limit: int = 100) -> list[dict]:
        """RECENT chats/groups (active within `days`): [{jid, name, is_group, ts}].

        /chats is recency-ordered + carries last_message_time, but its GROUP names are
        placeholders ('Group <id>'); the real names come from /user/my/groups, so we
        resolve them. Returns a small, relevant list for the model to choose from.

        Results are cached for _CHATS_CACHE_TTL seconds.  On a GOWA timeout or
        error the last cached result (even if stale) is returned rather than
        raising, so a slow gateway never blocks an agent turn for 30 s.
        """
        from datetime import datetime, timedelta, timezone

        cache_key = session_id or ""
        cached = _RECENT_CHATS_CACHE.get(cache_key)
        if cached is not None and (time.monotonic() - cached[0]) < _CHATS_CACHE_TTL:
            return cached[1]

        resp = self._request("GET", "/chats", device_id=session_id,
                             params={"limit": min(max(1, limit), 100)},
                             timeout=_CHATS_TIMEOUT)
        if resp is None:
            stale = _RECENT_CHATS_CACHE.get(cache_key)
            return stale[1] if stale is not None else []
        try:
            results = resp.json().get("results", {})
            data = (results.get("data") if isinstance(results, dict) else results) or []
        except Exception:
            stale = _RECENT_CHATS_CACHE.get(cache_key)
            return stale[1] if stale is not None else []
        try:
            gmap = {g["jid"]: g["name"] for g in self.list_my_groups(session_id=session_id)}
            contacts_map = self.live_contacts(session_id)
            cutoff = datetime.now(timezone.utc) - timedelta(days=days) if days else None
            out = []
            for c in data:
                jid = str(c.get("jid") or c.get("JID") or "")
                if not jid:
                    continue
                ts = c.get("last_message_time") or c.get("updated_at")
                if cutoff and ts:
                    try:
                        if datetime.fromisoformat(str(ts).replace("Z", "+00:00")) < cutoff:
                            continue
                    except Exception:
                        pass
                is_group = "@g.us" in jid
                name = _decrypt_chat_name(c.get("name") or c.get("Name"), jid)
                if is_group:
                    name = gmap.get(jid) or (name if name and not name.startswith("Group ")
                                             else gmap.get(jid, name) or _jid_to_number(jid))
                else:
                    # Overlay with live contact name; falls back to GOWA name or JID-derived.
                    name = contacts_map.get(jid) or name or _jid_to_number(jid)
                out.append({"jid": jid, "name": name, "is_group": is_group, "ts": ts})
            _RECENT_CHATS_CACHE[cache_key] = (time.monotonic(), out)
            return out
        except Exception as exc:
            logger.warning("gowa.recent_chats_failed", extra={"ctx_error": str(exc)})
            stale = _RECENT_CHATS_CACHE.get(cache_key)
            return stale[1] if stale is not None else []

    def list_my_groups(self, *, session_id: str | None = None) -> list[dict]:
        """ALL groups the user is in (up to 500): [{jid, name, is_group: True}].

        Uses /user/my/groups so a group is findable even if it's not among the
        recent chats returned by /chats (which is capped + recency-ordered)."""
        resp = self._request("GET", "/user/my/groups", device_id=session_id)
        if resp is None:
            return []
        try:
            results = resp.json().get("results", {})
            data = (results.get("data") or results.get("groups")
                    if isinstance(results, dict) else results) or []
            out = []
            for g in data:
                # This GOWA build returns Go-style Capitalized keys (JID, Name).
                jid = (g.get("JID") or g.get("jid") or g.get("group_jid") or g.get("id") or "")
                if not jid:
                    continue
                name = (g.get("Name") or g.get("name") or g.get("subject")
                        or g.get("Topic") or g.get("group_name") or _jid_to_number(str(jid)))
                out.append({"jid": str(jid), "name": name, "is_group": True})
            return out
        except Exception as exc:
            logger.warning("gowa.my_groups_failed", extra={"ctx_error": str(exc)})
            return []

    def live_contacts(self, session_id: str | None) -> dict[str, str]:
        """Live JID -> display name map from GET /user/my/contacts (TTL-cached 90s).

        Resolves names purely in RAM at call time — nothing is written to the DB.
        Parses defensively: tries the most common key names for both the JID field
        and the display-name field; first non-empty value wins per entry.
        Never raises — on error returns the last cached value or {}.
        """
        cache_key = session_id or ""
        cached = _CONTACTS_CACHE.get(cache_key)
        if cached is not None and (time.monotonic() - cached[0]) < _CHATS_CACHE_TTL:
            return cached[1]

        resp = self._request("GET", "/user/my/contacts", device_id=session_id,
                             timeout=_CONTACTS_TIMEOUT)
        if resp is None:
            stale = _CONTACTS_CACHE.get(cache_key)
            return stale[1] if stale is not None else {}
        try:
            body = resp.json()
            results = body.get("results", body)
            if isinstance(results, dict):
                data = (results.get("data") or results.get("contacts")
                        or results.get("list") or [])
            elif isinstance(results, list):
                data = results
            else:
                data = []
            out: dict[str, str] = {}
            for entry in data or []:
                if not isinstance(entry, dict):
                    continue
                jid = (entry.get("jid") or entry.get("chat_jid") or entry.get("id") or "")
                if not jid:
                    continue
                # First non-empty name field wins.
                name = (entry.get("full_name") or entry.get("name")
                        or entry.get("push_name") or entry.get("pushName")
                        or entry.get("business_name") or "")
                if name:
                    out[str(jid)] = str(name)
            _CONTACTS_CACHE[cache_key] = (time.monotonic(), out)
            return out
        except Exception as exc:
            logger.warning("gowa.live_contacts_failed", extra={"ctx_error": str(exc)})
            stale = _CONTACTS_CACHE.get(cache_key)
            return stale[1] if stale is not None else {}

    def get_group_participants(self, group_id: str, *, session_id: str | None = None) -> list[dict]:
        """List a group's members: [{name, number}]."""
        if "@g.us" not in (group_id or ""):
            return []
        resp = self._request("GET", "/group/participants", device_id=session_id,
                             params={"group_id": group_id})
        if resp is None:
            return []
        try:
            results = resp.json().get("results", {})
            data = (results.get("participants") or results.get("data")
                    if isinstance(results, dict) else results) or []
            out = []
            for p in data:
                jid = p.get("jid") or p.get("id") or ""
                number = p.get("phone_number") or (_jid_to_number(str(jid)) if jid else "")
                name = (p.get("display_name") or p.get("name") or p.get("pushName")
                        or p.get("push_name") or number)
                out.append({"name": name, "number": number,
                            "admin": bool(p.get("is_admin") or p.get("admin"))})
            return out
        except Exception as exc:
            logger.warning("gowa.participants_failed", extra={"ctx_error": str(exc)})
            return []

    def get_avatar(self, phone: str, *, session_id: str | None = None,
                   preview: bool = False) -> str | None:
        """Profile-picture URL of a WhatsApp user (None if private/not set)."""
        resp = self._request("GET", "/user/avatar", device_id=session_id,
                             params={"phone": _number_to_jid(phone),
                                     "is_preview": str(preview).lower()})
        if resp is None:
            return None
        try:
            r = resp.json().get("results", {})
            url = r.get("url") or r.get("avatar") or r.get("picture") if isinstance(r, dict) else None
            if isinstance(url, str) and url.startswith("http://"):
                url = "https://" + url[len("http://"):]
            return url or None
        except Exception:
            return None

    def get_user_info(self, phone: str, *, session_id: str | None = None) -> dict | None:
        """Public details for a phone: verified name, status/about, etc."""
        resp = self._request("GET", "/user/info", device_id=session_id,
                             params={"phone": _number_to_jid(phone)})
        if resp is None:
            return None
        try:
            r = resp.json().get("results", {})
            return r if isinstance(r, dict) else None
        except Exception:
            return None

    def list_devices(self) -> list[dict]:
        """All devices on the gateway: [{id, jid, state, ...}]."""
        resp = self._request("GET", "/devices")
        if resp is None:
            return []
        try:
            results = resp.json().get("results") or []
            return results if isinstance(results, list) else [results]
        except Exception:
            return []

    def device_info(self, session_id: str) -> dict | None:
        """Return the device record {id, state, jid, ...} from the gateway, or None."""
        resp = self._request("GET", "/devices", device_id=session_id)
        if resp is None:
            return None
        try:
            results = resp.json().get("results") or []
            if isinstance(results, dict):
                results = [results]
            return next((d for d in results if d.get("id") == session_id), None)
        except Exception:
            return None

    def session_status(self, session_id: str) -> str:
        """Return 'connected' | 'disconnected' | 'unknown'.

        Note: GOWA's `state` flips to "connected" as soon as the device's WhatsApp
        socket is up (i.e. while the QR is showing, before anyone scans). The real
        "linked to a phone" signal is a non-empty `jid`, so that's what we key on.
        """
        info = self.device_info(session_id)
        if info is None:
            return "unknown"
        return "connected" if info.get("jid") else "disconnected"

    def device_phone(self, session_id: str) -> str | None:
        """The linked phone number (from the device JID), once connected."""
        info = self.device_info(session_id)
        jid = (info or {}).get("jid")
        return _jid_to_number(jid) if jid else None

    def logout(self, session_id: str) -> None:
        self._request("GET", "/app/logout", device_id=session_id)

    def send_presence(self, chat_id: str, typing: bool, *, session_id: str | None = None) -> None:
        """Show/hide a 'typing…' indicator in the chat (so Mai feels responsive)."""
        self._request("POST", "/send/chat-presence", device_id=session_id,
                      json={"phone": _number_to_jid(chat_id),
                            "action": "start" if typing else "stop"})

    def mark_read(self, chat_id: str, message_id: str, *, session_id: str | None = None) -> None:
        """Mark the message as read (blue ticks / seen)."""
        if not message_id:
            return
        self._request("POST", f"/message/{message_id}/read", device_id=session_id,
                      json={"phone": _chat_jid(chat_id)})

    def get_chat_messages(self, session_id: str, chat_id: str, *, limit: int = 20) -> list[dict]:
        """Recent messages for a chat — served from Mai's ENCRYPTED store (decrypted
        in RAM), not GOWA. `session_id` is the gateway device id (== the account's
        WhatsAppConnection.session_id). Same dict shape as before so _chat_context /
        read_chat are unchanged. A shredded epoch renders text '[expired]'."""
        from sqlalchemy import select

        from backend.db.tenancy import WhatsAppConnection
        from backend.whatsapp import encstore

        s = SessionLocal()
        try:
            conn = s.execute(
                select(WhatsAppConnection).where(WhatsAppConnection.session_id == session_id)
            ).scalar_one_or_none()
            if conn is None:
                return []
            return encstore.recent(s, conn.account_id, chat_id, limit=limit)
        except Exception as exc:
            logger.warning("gowa.encstore_read_failed", extra={"ctx_error": str(exc)})
            return []
        finally:
            s.close()

    # -- inbound ------------------------------------------------------------
    def verify_signature(self, raw_body: bytes, signature_header: str | None) -> bool:
        if not self.webhook_secret:
            return not settings.is_production  # allow in dev, refuse in prod
        if not signature_header:
            return False
        provided = signature_header.split("sha256=", 1)[-1]
        expected = hmac.new(self.webhook_secret.encode(), raw_body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, provided)

    def parse_inbound(self, payload: dict) -> list[InboundMessage]:
        # Single-event shape: {"event","device_id","payload":{from,from_name,body,id}}
        if payload.get("event") and payload.get("event") != "message":
            return []  # ignore acks/reactions/edits/etc.
        device_id = payload.get("device_id")
        msg = payload.get("payload", payload)
        if not isinstance(msg, dict):
            return []
        # Keep the owner's OWN messages too — that's how the @Mai trigger arrives.
        from_me = bool(msg.get("is_from_me") or msg.get("fromMe") or msg.get("from_me"))
        sender = msg.get("from") or msg.get("sender") or ""
        text = msg.get("body") or msg.get("text") or msg.get("caption") or ""
        # Detect attached media; pull the caption out of the media object if needed,
        # and build the fetchable public url so the agent can transcribe/edit on demand.
        media_type = None
        media_url = None
        msg_id = str(msg.get("id") or "")
        for key, kind in (("image", "image"), ("video", "video"), ("audio", "audio"),
                          ("ptt", "audio"), ("voice", "audio"), ("document", "document")):
            mo = msg.get(key)
            if mo:
                media_type = kind
                if not text and isinstance(mo, dict):
                    text = mo.get("caption") or ""
                media_url = self._media_public_url(mo)
                # Log the media object's shape AND the path/url value, to wire media URLs.
                _mv = (mo.get("path") or mo.get("url") or "") if isinstance(mo, dict) else str(mo)
                logger.info("gowa.media_in", extra={
                    "ctx_type": kind,
                    "ctx_keys": list(mo.keys()) if isinstance(mo, dict) else type(mo).__name__,
                    "ctx_media": str(_mv)[:200],
                    "ctx_url": media_url or "",
                })
                break
        # Privacy 2B: a forked-GOWA payload carries epoch_id; `body`/caption is sealed.
        epoch_raw = msg.get("epoch_id")
        sealed_ct = None
        epoch_val = None
        if epoch_raw is not None:
            sealed_ct = text                      # the sealed blob (body or media caption)
            try:
                epoch_val = int(epoch_raw)
            except (TypeError, ValueError):
                epoch_val = None
            text = _decrypt_inbound(epoch_val, sealed_ct)   # plaintext for the turn (or "")
        # media resolved live-only for privacy; no pointer index
        if not sender or (not text and not sealed_ct and not media_type):
            return []  # need a sender AND some content (clear text, sealed blob, or media)
        # The conversation to reply into (recipient/group), falling back to sender.
        chat_raw = (
            msg.get("chat_id") or msg.get("chatId") or msg.get("to")
            or msg.get("chat") or msg.get("chatJID") or sender
        )
        # device_id can come back as a JID instead of the custom name; keep as-is.
        if device_id and "@" in str(device_id):
            device_id = str(device_id).split("@", 1)[0]
        return [
            InboundMessage(
                from_number=_jid_to_number(sender),
                text=text,
                message_id=msg_id,
                timestamp=str(msg.get("timestamp") or ""),
                session_id=device_id,
                from_me=from_me,
                chat_id=_chat_id(str(chat_raw)),
                from_name=(msg.get("from_name") or msg.get("pushName")
                           or msg.get("push_name") or None),
                media_type=media_type,
                media_url=media_url,
                epoch_id=epoch_val,
                sealed_ciphertext=sealed_ct,
                # Reply/quote: the gateway gives the quoted text + its id.
                quoted_text=(msg.get("quoted_body") or msg.get("quoted_message")
                             or msg.get("quotedBody") or None),
                quoted_id=(msg.get("replied_to_id") or msg.get("quoted_id") or None),
            )
        ]

    # -- outbound -----------------------------------------------------------
    @staticmethod
    def _msg_id(resp) -> str | None:
        if resp is None:
            return None
        try:
            r = resp.json().get("results", {})
            return r.get("message_id") or r.get("id") or None
        except Exception:
            return None

    def send_text(self, to_number: str, text: str, *, session_id: str | None = None,
                  reply_to: str | None = None) -> str | None:
        body = {"phone": _number_to_jid(to_number), "message": text}
        if reply_to:
            body["reply_message_id"] = reply_to
        return self._msg_id(self._request("POST", "/send/message", device_id=session_id, json=body))

    def send_image(self, to_number: str, image_url: str, *, caption: str | None = None,
                   session_id: str | None = None) -> str | None:
        body = {"phone": _number_to_jid(to_number), "image_url": image_url}
        if caption:
            body["caption"] = caption
        return self._msg_id(self._request("POST", "/send/image", device_id=session_id, json=body))

    def send_video(self, to_number: str, video_url: str, *, caption: str | None = None,
                   session_id: str | None = None) -> str | None:
        body = {"phone": _number_to_jid(to_number), "video_url": video_url}
        if caption:
            body["caption"] = caption
        return self._msg_id(self._request("POST", "/send/video", device_id=session_id, json=body))

    def send_audio(self, to_number: str, audio_url: str, *, session_id: str | None = None) -> str | None:
        body = {"phone": _number_to_jid(to_number), "audio_url": audio_url}
        return self._msg_id(self._request("POST", "/send/audio", device_id=session_id, json=body))

    def send_file(self, to_number: str, file_url: str, *, caption: str | None = None,
                  session_id: str | None = None) -> str | None:
        """Send a document. Unlike /send/image|audio (which take a URL), GOWA's
        /send/file requires the actual file UPLOADED as multipart — so we fetch the
        bytes from file_url and post them (no app-json header; httpx sets multipart)."""
        if not self.base_url:
            logger.info("gowa.no_gateway", extra={"ctx_path": "/send/file"})
            return None
        try:
            content = httpx.get(file_url, timeout=60).content
        except httpx.HTTPError as exc:
            logger.warning("gowa.file_fetch_failed", extra={"ctx_error": str(exc)})
            return None
        filename = (file_url.split("/")[-1].split("?")[0] or "file")
        params = {"device_id": session_id} if session_id else {}
        headers = {"X-Api-Key": self.api_key} if self.api_key else {}
        data = {"phone": _number_to_jid(to_number)}
        if caption:
            data["caption"] = caption
        try:
            resp = httpx.post(f"{self.base_url}/send/file", params=params, auth=self._auth(),
                              headers=headers, files={"file": (filename, content)},
                              data=data, timeout=60)
            if resp.status_code >= 400:
                logger.warning("gowa.http_error", extra={"ctx_status": resp.status_code,
                               "ctx_path": "/send/file", "ctx_body": resp.text[:200]})
                return None
            return self._msg_id(resp)
        except httpx.HTTPError as exc:
            logger.warning("gowa.http_exc", extra={"ctx_error": str(exc), "ctx_path": "/send/file"})
            return None

    def react(self, to_number: str, message_id: str, emoji: str, *,
              session_id: str | None = None) -> None:
        """React to a message with an emoji."""
        if not message_id:
            return
        self._request("POST", f"/message/{message_id}/reaction", device_id=session_id,
                      json={"phone": _number_to_jid(to_number), "emoji": emoji})

    def download_media(self, message_id: str, sender_phone: str, *,
                       session_id: str | None = None) -> tuple[bytes, str] | None:
        """Download a received message's media. Returns (bytes, filename) or None."""
        if not message_id:
            return None
        jid = (sender_phone if "@" in str(sender_phone)
               else _number_to_jid(sender_phone) + "@s.whatsapp.net")
        resp = self._request("GET", f"/message/{message_id}/download",
                             device_id=session_id, params={"phone": jid})
        if resp is None:
            return None
        try:
            results = resp.json().get("results", {})
        except Exception:
            return None
        filename = results.get("filename") or "media"
        # Some builds return base64 inline; otherwise a served file path.
        b64 = results.get("data") or results.get("base64")
        if b64:
            import base64
            try:
                return base64.b64decode(b64), filename
            except Exception:
                return None
        file_path = results.get("file_path") or results.get("path")
        if not file_path:
            return None
        try:
            r2 = httpx.get(f"{self.base_url}/{str(file_path).lstrip('/')}",
                           auth=self._auth(), timeout=60)
            if r2.status_code >= 400:
                logger.warning("gowa.media_fetch_error", extra={"ctx_status": r2.status_code})
                return None
            return r2.content, filename
        except httpx.HTTPError as exc:
            logger.warning("gowa.media_fetch_exc", extra={"ctx_error": str(exc)})
            return None

    def send_template(self, to_number: str, template: Template, *, session_id: str | None = None) -> None:
        body = template.variables[0] if template.variables else ""
        self.send_text(to_number, body, session_id=session_id)


def _jid_to_number(jid: str) -> str:
    """'628123@s.whatsapp.net' -> '+628123'."""
    num = jid.split("@", 1)[0].split(":", 1)[0].strip()
    return num if num.startswith("+") else "+" + num


def _number_to_jid(number: str) -> str:
    """'+628123' -> '628123' (country code + number, no suffix).

    Group chats ('xxx@g.us') are passed through unchanged so replies land in the
    group rather than a 1:1 chat.
    """
    if "@g.us" in number:
        return number
    return number.split("@")[0].lstrip("+").replace(" ", "").replace("-", "")


def _chat_id(raw: str) -> str:
    """Normalize a conversation id: keep group jids, reduce individuals to +number."""
    if "@g.us" in raw:
        return raw
    return _jid_to_number(raw)


def _chat_jid(chat_id: str) -> str:
    """Build the full WhatsApp JID the messages endpoint expects."""
    if "@" in chat_id:  # already a jid (group @g.us or @s.whatsapp.net)
        return chat_id
    return chat_id.lstrip("+").replace(" ", "").replace("-", "") + "@s.whatsapp.net"
