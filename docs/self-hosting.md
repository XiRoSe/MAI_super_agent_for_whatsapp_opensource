# Self-hosting MAI with Docker Compose

This is the primary self-host path. You end up with three containers:

| Service | What | Exposed |
|---|---|---|
| `mai` | API + agent + dashboard (built from this repo) | port 7777 on the host |
| `gowa` | WhatsApp gateway ([mai-gowa](https://github.com/XiRoSe/mai-gowa) fork, built from its GitHub repo) | internal only |
| `postgres` | Postgres 16 | internal only |

Before you start, read the [Disclaimer](../DISCLAIMER.md): the gateway is an
**unofficial** WhatsApp client and linking a number carries ban risk.

## 1. Prerequisites

- A host with Docker Engine + Compose v2 (a 1–2 GB VPS is fine)
- An LLM API key — [Together AI](https://www.together.ai/) is the default
  provider and also powers embeddings; Anthropic/OpenRouter work for chat but
  you still want a Together key for semantic recall (or disable it)
- A WhatsApp number you can afford to lose, with the phone nearby for QR pairing

## 2. Configure

```bash
git clone https://github.com/XiRoSe/MAI_super_agent_for_whatsapp_opensource.git mai
cd mai
cp .env.example .env
```

Required values in `.env`:

| Variable | Notes |
|---|---|
| `TOGETHER_API_KEY` | or another provider (Anthropic/OpenRouter) — but without a Together key, semantic recall auto-disables; add `SEMANTIC_RECALL_ENABLED=false` to silence the warning. See [configuration.md](configuration.md) |
| `MAI_JWT_SECRET` | `openssl rand -hex 32` |
| `MAI_ENCRYPTION_KEY` | `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |
| `GOWA_PASSWORD` | any strong password — compose wires it to both services |
| `GOWA_WEBHOOK_SECRET` | `openssl rand -hex 32` — ditto |
| `POSTGRES_PASSWORD` | recommended (defaults to `mai` otherwise) |
| `MAI_KEK` | recommended: `python -c "import os,base64; print(base64.b64encode(os.urandom(32)).decode())"` — without it, encrypted chat history doesn't survive restarts |

The compose file itself wires `MAI_DB_URL`, `WHATSAPP_PROVIDER=gowa`,
`WHATSAPP_GATEWAY_URL`, and the gateway's webhook back to MAI — you don't set
those by hand.

## 3. Run

```bash
docker compose up -d --build
docker compose logs -f mai        # wait for "app.ready"
```

First build takes a few minutes (it also builds the gateway from source).

## 4. Link WhatsApp

1. Open `http://<host>:7777` and register a dashboard account.
2. Go to the WhatsApp connection page and request the QR code.
3. On your phone: **WhatsApp → Settings → Linked devices → Link a device**, scan.
4. In any chat, send a message containing `@mai` (e.g. `@mai hello`). MAI replies
   in that chat, from your number.

MAI only reacts to *your own* messages containing `@mai` (plus guests in chats
you explicitly grant), so linking it doesn't change anything for your contacts.

## 5. Put it behind a reverse proxy (recommended)

The dashboard login and all API traffic should go over TLS. Any reverse proxy
works; Caddy is the least ceremony:

```
mai.example.com {
    reverse_proxy localhost:7777
}
```

Then set `PUBLIC_BASE_URL=https://mai.example.com` in `.env` and
`docker compose up -d` again. Do **not** expose the `gowa` or `postgres` ports;
the compose file already keeps them internal.

## 6. Backups

Two things hold state:

```bash
# 1) Postgres — all accounts, memory, schedules, skills, history
docker compose exec postgres pg_dump -U mai mai > mai-$(date +%F).sql

# 2) The gateway volume — the WhatsApp session (lose it => re-scan the QR)
docker run --rm -v mai_gowa_storage:/data -v "$PWD":/backup alpine \
  tar czf /backup/gowa-storage-$(date +%F).tgz -C /data .
```

Keep your `.env` somewhere safe too — `MAI_ENCRYPTION_KEY` and `MAI_KEK` are
needed to read the encrypted data in those backups.

## 7. Upgrading

```bash
git pull
docker compose up -d --build      # rebuilds mai (and gowa when its repo moved)
```

Schema changes are applied by the app at startup. Pre-1.0, check
[CHANGELOG.md](../CHANGELOG.md) before upgrading — minor versions may contain
breaking changes.

To force-rebuild the gateway against the latest mai-gowa:

```bash
docker compose build --no-cache gowa && docker compose up -d gowa
```

## Troubleshooting

- **MAI never reacts to `@mai`** — the trigger fires on your *own outgoing*
  messages, so the gateway must forward them:
  `WHATSAPP_WEBHOOK_INCLUDE_OUTGOING=true` (the compose file sets it) and you
  must be running the mai-gowa fork, not stock GOWA. Check
  `docker compose logs gowa` for webhook delivery errors.
- **`invalid signature` in mai logs** — `GOWA_WEBHOOK_SECRET` differs between the
  two services; with the provided compose file that only happens if you override
  `WHATSAPP_GATEWAY_WEBHOOK_SECRET` in `.env`.
- **Log warning about `TOGETHER_API_KEY` / semantic recall** — recall is on by
  default but needs the Together-hosted embedder; without the key it auto-disables
  for the process (memories/skills are all injected instead of only the relevant
  ones). Set the key, or set `SEMANTIC_RECALL_ENABLED=false` to silence it.
- **QR pairing loops** — remove and re-link the device on the phone, then
  request a fresh QR. If the gateway volume got corrupted, `docker compose down`,
  delete the `gowa_storage` volume, and pair again.
