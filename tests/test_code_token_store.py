"""Per-turn token store: mint, scope resolution, expiry, send cap."""

from __future__ import annotations

from backend.code_exec.token_store import TokenStore


def test_mint_and_get_resolves_tenant():
    s = TokenStore()
    tok = s.mint(account_id=5, user_id=2, device_id="dev1")
    ctx = s.get(tok)
    assert ctx is not None
    assert (ctx.account_id, ctx.user_id, ctx.device_id) == (5, 2, "dev1")


def test_get_unknown_token_is_none():
    assert TokenStore().get("nope") is None


def test_expired_token_is_none(monkeypatch):
    import backend.code_exec.token_store as ts

    s = ts.TokenStore(ttl_seconds=10)
    clock = {"t": 1000.0}
    monkeypatch.setattr(ts.time, "monotonic", lambda: clock["t"])
    tok = s.mint(1, 1, "d")
    clock["t"] = 1011.0  # 11s later, past the 10s ttl
    assert s.get(tok) is None


def test_spend_send_enforces_cap():
    s = TokenStore(send_cap=2)
    tok = s.mint(1, 1, "d")
    assert s.spend_send(tok) is True   # 1
    assert s.spend_send(tok) is True   # 2
    assert s.spend_send(tok) is False  # over cap
    assert s.spend_send("bad") is False


def test_mint_outbound_allowed_stored():
    s = TokenStore()
    tok = s.mint(account_id=1, user_id=1, device_id="d", outbound_allowed=True)
    assert s.get(tok).outbound_allowed is True
