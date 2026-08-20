"""Run an SDK function as the REAL tool, scoped to the token's tenant.

Returns STRUCTURED data (lists/dicts/strings) for the sandboxed code to process.
Outbound sends go through the token's send cap.
"""

from __future__ import annotations

from backend.code_exec.token_store import TokenContext, token_store
from backend.logging_config import get_logger

logger = get_logger(__name__)


class DispatchError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


_SELF = {"", "me", "myself", "my", "self", "owner", "i", "my number", "my profile"}


def _provider():
    from backend.whatsapp import get_provider
    return get_provider()


def _owner_phone(ctx: TokenContext, session) -> str:
    """The owner's own number, resolved from the AUTHENTICATED token (not caller input)."""
    from backend.db.tenancy import User
    u = session.get(User, ctx.user_id)
    return (u.whatsapp_number if u and u.whatsapp_number else "") or ""


def _resolve_person(p, dev: str, ctx: TokenContext, session, who: str) -> str:
    """Map a person reference to a number/jid. 'me'/self -> the owner (from the token);
    a number or jid -> itself; a contact name -> that 1:1 chat's jid."""
    w = (who or "").strip()
    if w.lower() in _SELF:
        return _owner_phone(ctx, session)
    if "@" in w or any(ch.isdigit() for ch in w):
        return w
    return _resolve_chat(p, dev, w) or w


def _resolve_chat(provider, device_id: str, chat: str) -> str | None:
    """A name or an id -> a jid. Ids (contain '@') pass through."""
    if "@" in (chat or ""):
        return chat
    q = (chat or "").strip().lower()
    pool = []
    try:
        pool += provider.recent_chats(session_id=device_id, days=365)
    except Exception:
        pass
    try:
        pool += provider.list_my_groups(session_id=device_id)
    except Exception:
        pass
    exact = [c for c in pool if (c.get("name") or "").lower() == q]
    if exact:
        return exact[0]["jid"]
    contains = [c for c in pool if q and q in (c.get("name") or "").lower()]
    return contains[0]["jid"] if len(contains) == 1 else None


def dispatch(fn: str, args: dict, ctx: TokenContext, session, token: str):
    dev = ctx.device_id
    p = _provider()

    if fn == "find_chats":
        chats = p.recent_chats(session_id=dev, days=int(args.get("days", 7)),
                               limit=int(args.get("limit", 50)))
        q = (args.get("query") or "").strip().lower()
        kind = (args.get("kind") or "any").lower()

        def ok(c):
            if kind in ("group", "groups") and not c.get("is_group"):
                return False
            if kind in ("dm", "direct") and c.get("is_group"):
                return False
            return not q or q in (c.get("name") or "").lower()
        return [{"name": c.get("name"), "id": c.get("jid"), "is_group": c.get("is_group")}
                for c in chats if ok(c)]

    if fn == "read_chat":
        jid = _resolve_chat(p, dev, args.get("chat", ""))
        if not jid:
            raise DispatchError(f"no chat matches {args.get('chat')!r}")
        msgs = p.get_chat_messages(dev, jid, limit=int(args.get("limit", 20)))
        # Attach a per-message send-time in the reader's local timezone (empty when the
        # raw ts is unparseable) so sandboxed code can reason about when things were said.
        from backend.db.tenancy import User
        from backend.whatsapp.timefmt import msg_time_label

        u = session.get(User, ctx.user_id)
        tz_name = getattr(u, "timezone", None) if u else None
        for m in msgs:
            m["time"] = msg_time_label(m.get("ts"), tz_name)
        return msgs

    if fn == "get_group_members":
        jid = _resolve_chat(p, dev, args.get("group", ""))
        if not jid or "@g.us" not in jid:
            raise DispatchError(f"{args.get('group')!r} is not a group")
        return p.get_group_participants(jid, session_id=dev)

    if fn == "get_profile_picture":
        who = _resolve_person(p, dev, ctx, session, args.get("phone", ""))
        return p.get_avatar(who, session_id=dev) or ""

    if fn == "get_user_details":
        who = _resolve_person(p, dev, ctx, session, args.get("phone", ""))
        return p.get_user_info(who, session_id=dev) or {}

    if fn == "web_search":
        from agno.tools.websearch import WebSearchTools
        return WebSearchTools(fixed_max_results=5).web_search(args.get("query", ""))

    if fn == "read_url":
        from backend.tools.web_tools import WebReadTools
        return WebReadTools().read_url(args.get("url", ""))

    if fn in ("run_model", "search_models", "get_model_schema"):
        from backend.tools.replicate_tools import ReplicateTools
        rt = ReplicateTools()
        if fn == "search_models":
            return rt.search_models(args.get("query", ""))
        if fn == "get_model_schema":
            return rt.get_model_schema(args.get("model", ""))
        return rt.run_model(args.get("model", ""), args.get("inputs") or {})

    if fn == "show_image":
        if not ctx.current_chat:
            raise DispatchError("no current chat to show the image in")
        p.send_image(ctx.current_chat, args.get("url", ""),
                     caption=args.get("caption") or None, session_id=dev)
        return {"shown": True, "to": ctx.current_chat}

    if fn in ("remember", "recall", "forget"):
        from backend.db.tenancy import User
        from backend.tools.memory_tools import MemoryTools
        mt = MemoryTools(user=session.get(User, ctx.user_id))
        if fn == "remember":
            return mt.remember(args.get("fact", ""), args.get("topics") or "")
        if fn == "recall":
            return mt.recall(args.get("query") or "")
        return mt.forget(args.get("snippet", ""))

    if fn in ("send_message", "send_media"):
        if not ctx.outbound_allowed:
            raise DispatchError("sends not approved for this run")
        if not token_store.spend_send(token):
            raise DispatchError("send cap reached for this script run")
        jid = _resolve_chat(p, dev, args.get("chat", ""))
        if not jid:
            raise DispatchError(f"no chat matches {args.get('chat')!r}")
        if fn == "send_message":
            p.send_text(jid, args.get("text", ""), session_id=dev)
        else:
            p.send_image(jid, args.get("url", ""), caption=args.get("caption") or None,
                         session_id=dev)
        logger.info("sdk.sent", extra={"ctx_account": ctx.account_id, "ctx_fn": fn})
        return {"sent": True, "to": jid}

    raise DispatchError(f"unknown function {fn!r}")
