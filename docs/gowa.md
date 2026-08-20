# The WhatsApp gateway: mai-gowa

Mai does not speak the WhatsApp protocol itself. It talks to
[**mai-gowa**](https://github.com/XiRoSe/mai-gowa) — a fork of
[aldinokemal/go-whatsapp-web-multidevice](https://github.com/aldinokemal/go-whatsapp-web-multidevice)
("GOWA", MIT), which is a Go REST wrapper around
[whatsmeow](https://github.com/tulir/whatsmeow), the library implementing the
WhatsApp Web multi-device protocol.

This is an **unofficial** client: it pairs to your number as a linked device via
QR code, exactly like WhatsApp Web. No Meta business account, no per-message
fees, no 24-hour window or template approval — and correspondingly, no official
support and a real risk of the number being banned
(see [DISCLAIMER.md](../DISCLAIMER.md)).

## Why a fork? What Mai needs that stock GOWA doesn't have

**Mai requires the fork.** Pointing it at upstream GOWA (or WAHA/Evolution)
will connect but the product won't function. The fork adds:

1. **Multi-device (multi-number) routing.** Every REST call takes a `device_id`
   parameter and every webhook payload carries a top-level `device_id`, so one
   gateway instance hosts many numbers. Mai maps each account's session id to a
   gateway `device_id` — that's how a multi-tenant Mai (or just you plus a test
   number) shares one gateway.
2. **Outgoing (`from_me`) webhook events.** Mai's entire trigger model is "the
   owner tagged `@mai` in their own message" — which is an *outgoing* message
   from the gateway's point of view. The fork forwards the owner's own messages
   to the webhook (keep `WHATSAPP_WEBHOOK_INCLUDE_OUTGOING=true`); stock GOWA's
   webhook stream was built around inbound traffic, so the trigger simply never
   fires there.
3. **Sealed payloads (optional).** The fork can encrypt message bodies (and
   stored chat names, on the wire as `NSEAL:<epoch>:<ciphertext>`) as NaCl
   sealed boxes to Mai's current epoch public key, which it fetches from Mai's
   token-gated `GET /internal/seal-pubkey`. Combined with Mai's 2-day key epochs
   and crypto-shredding, message content at rest is unreadable to the host after
   the retention window. Go's `nacl/box.SealAnonymous` output opens unchanged in
   Mai's Python (`backend/crypto/sealing.py`).

## The contract, at a high level

**Mai → gateway (REST, basic auth, `?device_id=...` on every call):**

- create/list devices, `GET /app/login` (returns the QR link), logout, reconnect,
  connection status
- `POST /send/message` (and media variants) — how every reply and scheduled
  message goes out
- chat/message listing used for "read recent context" and cross-chat tools

**Gateway → Mai (webhook):**

- `POST /webhooks/whatsapp` on the Mai service
- Signed with HMAC-SHA256 over the raw body, sent as `X-Hub-Signature-256:
  sha256=<hex>`; the secret is `WHATSAPP_WEBHOOK_SECRET` on the gateway and
  `WHATSAPP_GATEWAY_WEBHOOK_SECRET` on Mai — they must match. Mai rejects bad
  signatures with 403 (and in `MAI_ENV=production`, refuses unsigned traffic
  entirely).
- Events: `message` events (including the owner's own, flagged `from_me` /
  `is_from_me`), each with `device_id`, chat/sender ids, text, and media
  references. Non-message events (acks, reactions, edits) are ignored by Mai's
  parser.
- Mai answers `200` immediately and processes the turn in a background task, so
  slow model calls never cause gateway retries.

## Configuration summary

| Where | Variable | Meaning |
|---|---|---|
| gateway | `APP_BASIC_AUTH` | `user:pass` Mai uses for REST calls |
| gateway | `APP_OS` | Device name shown in "Linked devices" (use `Mai`) |
| gateway | `WHATSAPP_WEBHOOK` | `http://mai:7777/webhooks/whatsapp` |
| gateway | `WHATSAPP_WEBHOOK_SECRET` | HMAC secret (must match Mai's) |
| gateway | `WHATSAPP_WEBHOOK_INCLUDE_OUTGOING` | `true` — required for the `@mai` trigger |
| gateway | volume at `/app/storages` | WhatsApp session + chat DBs; losing it means re-pairing |
| Mai | `WHATSAPP_PROVIDER=gowa`, `WHATSAPP_GATEWAY_URL`, `WHATSAPP_GATEWAY_USER`, `WHATSAPP_GATEWAY_PASSWORD`, `WHATSAPP_GATEWAY_WEBHOOK_SECRET` | see [configuration.md](configuration.md) |

The provided [docker-compose.yml](../docker-compose.yml) wires all of this; the
tables are for when you run the gateway elsewhere.

## Keeping the fork current

mai-gowa tracks upstream GOWA and keeps its MIT license and history, so upstream
fixes (whatsmeow protocol updates matter — WhatsApp changes things) can be
merged. Gateway bugs that reproduce on upstream GOWA should be reported
[upstream](https://github.com/aldinokemal/go-whatsapp-web-multidevice/issues);
fork-specific issues (device routing, outgoing events, sealing) go to
[mai-gowa](https://github.com/XiRoSe/mai-gowa/issues) or this repo.
