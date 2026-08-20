# Disclaimer & acceptable use

## Not affiliated with WhatsApp or Meta

Mai is an independent project. It is **not affiliated with, associated with,
authorized, endorsed by, or in any way officially connected with WhatsApp,
WhatsApp LLC, or Meta Platforms, Inc.** "WhatsApp" and related names and marks
are trademarks of WhatsApp LLC / Meta Platforms, Inc., used here only to
describe interoperability.

## Unofficial client — account risk

Mai connects to WhatsApp through an unofficial gateway
([mai-gowa](https://github.com/XiRoSe/mai-gowa), a fork of GOWA built on
[whatsmeow](https://github.com/tulir/whatsmeow)) that emulates the WhatsApp Web
multi-device protocol, pairing to your number as a linked device.

Using unofficial clients may violate WhatsApp's Terms of Service. Numbers using
them **can be warned, temporarily blocked, or permanently banned** — sometimes
without any automated-looking behavior, and without appeal. Concretely:

- **Use a number you can afford to lose.** Do not link your only business
  number or a number tied to accounts you can't recover.
- The risk is yours. Neither the Mai maintainer nor the gateway authors can
  predict, prevent, or reverse a ban.
- If you need an officially supported integration, use the WhatsApp Business
  Platform / Cloud API instead — Mai's core "@mai on your own number" model is
  simply not possible there.

## Acceptable use

Mai is designed to be **your own personal assistant on your own account**. By
using it you agree to:

- **No spam**: no unsolicited, bulk, marketing, or automated broadcast
  messaging of any kind;
- **No surveillance**: no monitoring, scraping, or collecting data about people
  without their knowledge and consent, no harassment, no stalkerware;
- complying with WhatsApp's Terms of Service, applicable law (including privacy
  law such as the GDPR where it applies to the people whose messages your
  instance processes), and the rights of the people you message.

The maintainer does not condone misuse and accepts no liability for it.

## Data & privacy (self-hosted)

When you self-host, **you are the data controller**:

- Mai stores chat context, memory, schedules, and credentials in *your*
  database and volumes. Message bodies in the encrypted store are sealed to
  rotating epoch keys and crypto-shredded after a retention window.
- **Mai sends no telemetry** to the maintainer. Nothing leaves your server
  except the API calls you configure.
- The **LLM provider you configure** (Together AI, Anthropic, OpenRouter, ...)
  receives the message content Mai processes, under your API key and their
  terms. The same applies to optional services you enable: Replicate (media),
  E2B (code sandbox), web search, Google/Microsoft integrations.
- You are responsible for securing your deployment: TLS, firewalling the
  gateway and database, strong secrets, backups, and updates.

## No warranty

Mai is provided **"as is"**, without warranty of any kind, as stated in the
[MIT License](LICENSE). The maintainer is not liable for banned accounts, lost
messages, model costs, data loss, or anything else arising from use of this
software.
