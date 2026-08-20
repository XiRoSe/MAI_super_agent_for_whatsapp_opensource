"""Test that parse_inbound no longer calls media_index.remember."""

import backend.whatsapp.gowa as g


def test_parse_inbound_does_not_call_media_index(monkeypatch):
    """Verify parse_inbound does NOT persist to media_index."""
    import backend.whatsapp.media_index as mi

    monkeypatch.setattr(
        mi, "remember",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not persist"))
    )
    prov = g.GowaWhatsAppProvider()
    payload = {
        "event": "message",
        "device_id": "mai_acct_5",
        "payload": {
            "id": "X",
            "from": "123@s.whatsapp.net",
            "image": {"path": "statics/media/x.jpg"},
            "body": "@Mai describe it"  # add body so it's not filtered out
        }
    }
    # Must not raise AssertionError
    result = prov.parse_inbound(payload)
    assert len(result) == 1
    assert result[0].message_id == "X"
