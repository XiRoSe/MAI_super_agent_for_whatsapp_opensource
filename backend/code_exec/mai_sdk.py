"""The `mai` SDK — uploaded INTO the sandbox and imported there as `mai`.

Each function is a thin client that POSTs to our app's /internal/sdk/{fn}. The
real work runs in our app (tenant-scoped). Functions return structured data.
Keep each docstring to ONE rich line — it becomes the model's reference.
"""

from __future__ import annotations

import os

import httpx

OUTBOUND: set[str] = set()  # code_exec is fetch/compute only; SENDING is done by direct tools


def _call(fn: str, **args):
    base = os.environ.get("MAI_SDK_BASE_URL", "").rstrip("/")
    token = os.environ.get("MAI_SDK_TOKEN", "")
    payload = {k: v for k, v in args.items() if v is not None}
    resp = httpx.post(f"{base}/internal/sdk/{fn}", json=payload,
                      headers={"Authorization": f"Bearer {token}"}, timeout=290)
    resp.raise_for_status()
    return resp.json().get("result")


def find_chats(query=None, kind=None, days=None, limit=None):
    """Find the user's recent WhatsApp chats & groups. Args: query (name fragment), kind ('group'|'dm'|'any'), days back (default 7; widen to reach older ones), limit. Returns [{name, id, is_group}]. Use FIRST to locate a chat before reading or messaging it. Matching is same-script substring: a contact may be saved in a different script/language than the query (e.g. 'danny' typed in Latin letters but saved in the contact's native script) — if a name returns [], retry with its spelling in the other script."""
    return _call("find_chats", query=query, kind=kind, days=days, limit=limit)


def read_chat(chat, limit=None):
    """Read a chat/group's recent messages — pass its name or id (from find_chats). Returns [{sender, text, time, from_me, media_type, media_url}]. `time` is the message's send-time in the user's local timezone (e.g. 'today 09:14', 'yesterday 22:01', '03/06 18:40'; '' if unknown). media_url is a fetchable public URL for the media bytes (feed it into run_model to transcribe/describe/edit on demand); it may be None for old/un-downloaded media. Use to summarize or check what was said."""
    return _call("read_chat", chat=chat, limit=limit)


def get_group_members(group):
    """List ALL members of a group (its name or id). Returns [{name, number, admin}]. Use for 'who is in X', counting members, or finding someone's number."""
    return _call("get_group_members", group=group)


def get_profile_picture(phone):
    """Get a person's profile-picture URL — pass their phone number, or 'me' for the user themselves. Returns a URL ('' if none). Feed the URL into run_model to stylize/cartoon it, or to EDIT it (add/change something) with an instruction-based image-edit model that keeps the original recognizable."""
    return _call("get_profile_picture", phone=phone)


def get_user_details(phone):
    """Get a person's public WhatsApp details (name, about/status) — phone number or 'me'. Returns a dict."""
    return _call("get_user_details", phone=phone)


def web_search(query):
    """Search the web for current info/facts/news you don't already know. Returns [{title, url, body}]."""
    return _call("web_search", query=query)


def read_url(url):
    """Fetch and read a specific URL/link — returns the page's text, or a description if it's an image. Use when the user shares or refers to a link."""
    return _call("read_url", url=url)


def search_models(query):
    """Search Replicate's model catalog by keyword (e.g. 'cartoon', 'face to sticker', 'upscale'). Returns matching model ids to use with run_model."""
    return _call("search_models", query=query)


def get_model_schema(model):
    """Get a Replicate model's INPUT parameters — every field's name, type, default, whether it's required, and any option lists. Use after search_models and BEFORE run_model to see EVERY knob you can set (image, prompt, strength, guidance, steps, aspect_ratio, seed, output_format, ...) and call run_model with full, precise control. Don't guess inputs — read the schema, then pass exactly what you want."""
    return _call("get_model_schema", model=model)


def run_model(model, inputs=None):
    """Run ANY Replicate model in the backend. Args: model ('owner/name'), inputs (dict — pass ANY of the model's parameters freely; nothing is filtered). Returns the output (usually an image/file URL). YOU choose the model — nothing is pinned. For precise control, call get_model_schema(model) first to learn all its fields. Use for a NEW image from text (pick a text-to-image model), image-to-image, stylize/cartoon, edit, upscale, transcribe, etc. IMPORTANT — to EDIT a real photo while KEEPING the people/scene recognizable (add/remove/change something, e.g. 'add a rubber duck next to them'), use an INSTRUCTION-BASED image-EDIT model (search_models for an 'edit' / 'kontext'-style model) with {'image': original_url, 'prompt': '<the edit>'} plus whatever strength/guidance the schema offers — it preserves the original. A text-to-image or heavy stylization model will NOT keep the likeness, so don't use those when resemblance matters."""
    return _call("run_model", model=model, inputs=inputs or {})


def remember(fact, topics=None):
    """Save a durable fact about the user to long-term memory (e.g. a preference, a name, a goal). Use when the user tells you something worth keeping."""
    return _call("remember", fact=fact, topics=topics)


def recall(query=None):
    """Recall saved facts from long-term memory, optionally filtered by a query. Returns a list of facts."""
    return _call("recall", query=query)


def forget(snippet):
    """Delete a saved memory that matches the given snippet."""
    return _call("forget", snippet=snippet)


# NOTE: sending messages/media is NOT in the SDK — it happens OUTSIDE code_exec via Mai's
# direct send tools, AFTER it has read and assessed the script's results.
