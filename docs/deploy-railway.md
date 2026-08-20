# Deploy MAI on Railway

The repo ships `railway.json` (Dockerfile builder + start command), so Railway
builds and runs it with no extra config files (`railway.json` takes precedence;
the `Procfile` is only used by buildpack platforms). You will
create **one project with three services**: `mai`, Postgres, and the
[mai-gowa](https://github.com/XiRoSe/mai-gowa) gateway.

## 1. The `mai` service

1. Fork this repo (or connect it directly): **New Project → Deploy from GitHub
   repo** → pick `MAI_super_agent_for_whatsapp_opensource`.
2. Railway detects the `Dockerfile` via `railway.json` and builds the single
   image (frontend + backend). The start command already handles Railway's
   injected `$PORT` — note it is wrapped in `sh -c` because `${PORT}` expansion
   only happens inside a shell:
   `sh -c "uvicorn backend.app:app --host 0.0.0.0 --port ${PORT:-7777}"`.
3. Under **Settings → Networking**, generate a public domain
   (e.g. `mai-production.up.railway.app`).

## 2. Postgres

**+ New → Database → PostgreSQL.** Then on the `mai` service add:

```
MAI_DB_URL=postgresql+psycopg://${{Postgres.PGUSER}}:${{Postgres.PGPASSWORD}}@${{Postgres.RAILWAY_PRIVATE_DOMAIN}}:5432/${{Postgres.PGDATABASE}}
```

(Reference syntax `${{Postgres.*}}` resolves at deploy time. MAI uses the
psycopg3 driver; plain `postgresql://` and `postgres://` URLs are normalized
automatically, so `MAI_DB_URL=${{Postgres.DATABASE_URL}}` also works — prefer
the private-domain form above to keep traffic off the public network.)

## 3. The gateway service (mai-gowa)

1. **+ New → GitHub repo →** `XiRoSe/mai-gowa`. Set the Dockerfile path to
   `docker/golang.Dockerfile` (Settings → Build) if Railway doesn't pick it up.
2. **Add a volume** mounted at `/app/storages` — this holds the WhatsApp
   session; without it every redeploy unlinks your number.
3. Variables:

```
APP_PORT=3000
APP_OS=MAI
APP_BASIC_AUTH=mai:<strong password>
WHATSAPP_WEBHOOK=http://<mai private domain>:<mai PORT>/webhooks/whatsapp
WHATSAPP_WEBHOOK_SECRET=<random hex, e.g. openssl rand -hex 32>
WHATSAPP_WEBHOOK_INCLUDE_OUTGOING=true
```

   For `WHATSAPP_WEBHOOK`, prefer the **private** network:
   `http://${{mai.RAILWAY_PRIVATE_DOMAIN}}:${{mai.PORT}}/webhooks/whatsapp`
   (set an explicit `PORT` variable, e.g. `7777`, on the `mai` service so the
   reference resolves). The public URL
   (`https://<mai public domain>/webhooks/whatsapp`) also works.
4. Do **not** give the gateway a public domain unless you must — its UI/API is
   basic-auth only.

## 4. `mai` service variables

Minimum set (full reference: [configuration.md](configuration.md)):

```
MAI_ENV=production
MAI_DB_URL=...                         # from step 2
TOGETHER_API_KEY=...                   # LLM + embeddings
MAI_JWT_SECRET=...                     # openssl rand -hex 32
MAI_ENCRYPTION_KEY=...                 # Fernet key
MAI_KEK=...                            # base64 of 32 random bytes — REQUIRED in production
SEAL_PUBKEY_TOKEN=...                  # random — required in production if the gateway seals payloads
WHATSAPP_PROVIDER=gowa
WHATSAPP_GATEWAY_URL=http://${{gowa.RAILWAY_PRIVATE_DOMAIN}}:3000
WHATSAPP_GATEWAY_USER=mai
WHATSAPP_GATEWAY_PASSWORD=<same as APP_BASIC_AUTH password>
WHATSAPP_GATEWAY_WEBHOOK_SECRET=<same as WHATSAPP_WEBHOOK_SECRET>
PUBLIC_BASE_URL=https://<mai public domain>
PORT=7777
```

`MAI_ENV=production` turns on the fail-closed checks: the app will refuse
unsigned webhooks and refuse to start subsystems whose secrets are missing —
that's what you want on a public deployment. Railway's **sealed variables** are
a good fit for `MAI_KEK` (write-only after creation).

## 5. Pair and test

Open `https://<mai public domain>`, register, scan the QR from the WhatsApp
connection page, and send `@mai hello` in any chat.

## Gotchas

- **`${PORT}` expansion needs `sh -c`.** If you override the start command,
  keep the `sh -c "... ${PORT:-7777}"` wrapper; a bare `uvicorn --port $PORT`
  is not expanded by Railway's exec-style runner.
- **Renaming a service changes its domains.** Both the public
  `*.up.railway.app` domain and the private `<service>.railway.internal` domain
  derive from the service name — if you rename `mai` or `gowa`, update
  `WHATSAPP_WEBHOOK`, `WHATSAPP_GATEWAY_URL`, and `PUBLIC_BASE_URL`.
- **Private networking is IPv6.** Recent GOWA/uvicorn versions handle it, but if
  the services can't reach each other on private domains, fall back to public
  URLs while debugging.
- **The gateway volume is not optional.** No volume ⇒ the WhatsApp session is
  wiped on every deploy and you re-scan the QR.
- **Watch your LLM spend.** A public production instance with real users
  multiplies model calls; quotas (`QUOTA_MESSAGES_PER_DAY`,
  `QUOTA_TOKENS_PER_MONTH`) are your throttle.
