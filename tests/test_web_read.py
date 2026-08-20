"""read_url: fetch a page (text) or image (description)."""

from __future__ import annotations

import backend.tools.web_tools as wt
from backend.tools.web_tools import WebReadTools, _extract_text


def test_extract_text_strips_tags_and_scripts():
    out = _extract_text("<html><head><title>x</title></head><body><p>Hello world</p>"
                        "<script>evil()</script></body></html>")
    assert "Hello world" in out and "evil" not in out


def test_read_url_html(monkeypatch):
    class R:
        status_code = 200
        headers = {"content-type": "text/html; charset=utf-8"}
        text = "<p>This is the page body</p>"
        content = b""

    monkeypatch.setattr(wt.httpx, "get", lambda *a, **k: R())
    out = WebReadTools().read_url("example.com")
    assert "This is the page body" in out and "UNTRUSTED" in out


def test_read_url_image(monkeypatch):
    class R:
        status_code = 200
        headers = {"content-type": "image/jpeg"}
        text = ""
        content = b"\xff\xd8\xff"

    monkeypatch.setattr(wt.httpx, "get", lambda *a, **k: R())
    monkeypatch.setattr("backend.whatsapp.media_in.describe_media", lambda *a, **k: "a red car")
    out = WebReadTools().read_url("https://x/y.jpg")
    assert "red car" in out
