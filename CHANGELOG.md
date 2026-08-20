# Changelog

All notable changes to MAI are documented here.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [SemVer](https://semver.org/) — pre-1.0, **minor versions may
contain breaking changes**; read the notes before upgrading.

## [Unreleased]

## [0.1.0] - 2026-08-20

First public release. MAI is an MVP: functional, tested, and honest about its
rough edges.

### Added

- **@mai on your own number**: MAI links to the owner's WhatsApp via the
  [mai-gowa](https://github.com/XiRoSe/mai-gowa) gateway fork, stays silent, and
  acts only when the owner tags `@mai` in any chat — reading recent chat context
  and replying as the owner, in that chat.
- Cross-chat actions: send/forward/look up in the owner's other chats and groups.
- Natural-language scheduling (one-off and recurring), fired by an in-process
  cron poller, timezone-aware, with per-user limits.
- Per-user skills as editable `SKILL.md` packages, seeded from a bundled starter
  set (morning brief, inbox triage, photo edit, knowledge base, and more).
- Agentic long-term memory plus semantic recall (only relevant memories/skills
  are injected per turn) and a background per-user "understanding" reflection.
- Delegated per-chat access: the owner can grant chats so other people can
  invoke `@mai` there with a restricted, stateless toolset.
- Media: reads inbound images and voice notes, generates and sends images/video
  via Replicate.
- Optional sandboxed Python execution (E2B, or the bundled self-hosted
  `runner/` service) with guardrails, send caps, and token-scoped callbacks.
- Web dashboard (React + Ant Design) served from the same container: chat,
  schedules, skills, memory, WhatsApp connection (QR), settings.
- Model-provider factory: Together AI (default), Anthropic, OpenRouter, with an
  automatic fallback model option.
- Security posture: fail-closed tenant scoping, HMAC-verified webhooks,
  encrypted time-windowed message store with rotating epoch keys and
  crypto-shredding, encrypted credential vault, JWT dashboard auth.
- Deployment: single Docker image (SPA + API), `docker-compose.yml` for
  self-hosting, Railway config (`railway.json`, `Procfile`).

### Known limitations

- Requires the mai-gowa gateway fork; stock GOWA/WAHA/Evolution won't trigger.
- Unofficial WhatsApp client — account ban risk is real (see DISCLAIMER.md).
- Multi-tenant codebase without a dedicated single-tenant mode yet.
- Solo-maintained; no SLA.

[Unreleased]: https://github.com/XiRoSe/MAI_super_agent_for_whatsapp_opensource/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/XiRoSe/MAI_super_agent_for_whatsapp_opensource/releases/tag/v0.1.0
