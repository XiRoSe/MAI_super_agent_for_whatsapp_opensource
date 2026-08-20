# Security Policy

## Supported versions

Only the latest release line receives security fixes.

| Version | Supported |
| ------- | --------- |
| latest 0.x release | yes |
| anything older | no — please upgrade |

## Reporting a vulnerability

**Please do not open public issues for security problems.**

Use GitHub's private vulnerability reporting:
**Security → Report a vulnerability** on
<https://github.com/XiRoSe/MAI_super_agent_for_whatsapp_opensource/security/advisories/new>.

Include: affected version or commit, deployment method (compose / Railway /
other), reproduction steps, and impact. Please redact real phone numbers and
message content from any logs you attach.

## What to expect

Mai is maintained by a single developer. These are best-effort targets, not
guarantees:

- Acknowledgement within **5 business days**
- Triage and severity assessment within **14 days**
- A fix or mitigation for confirmed high/critical issues as soon as practical,
  released as a patch version with a GitHub Security Advisory and a
  CHANGELOG entry

Coordinated disclosure: please allow up to **90 days** before publishing
details.

## Scope notes

- Mai reaches WhatsApp through an unofficial gateway,
  [mai-gowa](https://github.com/XiRoSe/mai-gowa), a fork of
  [GOWA](https://github.com/aldinokemal/go-whatsapp-web-multidevice) built on
  [whatsmeow](https://github.com/tulir/whatsmeow). If a gateway issue reproduces
  on stock upstream GOWA, please also (or instead) report it upstream; issues in
  the fork's additions (device routing, outgoing events, payload sealing) belong
  here or on the mai-gowa repo.
- Self-hosted deployments are configured by you. Misconfiguration — exposed
  gateway/Postgres ports, default passwords you didn't change, no TLS — is out
  of scope unless the shipped defaults themselves are unsafe (that *is* in
  scope; tell us).
- Prompt-injection reports are welcome: Mai treats forwarded chat content as
  untrusted input and has tests around that boundary, but it's an evolving area
  and an LLM-based assistant can never be fully injection-proof.
