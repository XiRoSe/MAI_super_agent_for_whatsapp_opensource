---
name: scheduling-etiquette
description: Use when the user asks Mai to do something later or on a recurring basis (reminders, schedules, "every morning", "remind me", "tomorrow at"). Covers timezone capture, confirming ambiguous times, and choosing static vs dynamic schedules.
---

# Scheduling etiquette

When a user asks for something timed, follow this procedure before touching the
schedule tools.

## 1. Know the timezone first
A bare time like "8am" is meaningless without a timezone.
- If the user's timezone is unknown, ask once: "What timezone are you in?" then
  call `set_timezone` with the IANA name (e.g. `Europe/London`).
- Never create a schedule without a known timezone.

## 2. Resolve and confirm ambiguous phrasing
Words like "tonight", "later", "the morning", "every morning" are ambiguous.
- Resolve them to a concrete local time or cron.
- Echo the resolved time back and get a yes BEFORE creating, e.g.
  "So that's every weekday at 08:00 America/New_York — shall I set it?"
- Unambiguous requests ("tomorrow at 3pm", "in 2 hours") can be created directly,
  but still state what you set.

## 3. Static vs dynamic
- **static** — a fixed message at the time ("remind me to call the bank").
  `kind="static"`, `action_text` = the reminder text.
- **dynamic** — re-run a task at fire time and send the result
  ("every morning summarize my day and my first meeting").
  `kind="dynamic"`, `action_text` = the instruction to run.

## 4. One-off vs recurring
- one-off: `schedule_type="once"`, `run_at` = ISO local datetime.
- recurring: `schedule_type="recurring"`, `cron` = 5-field cron in local time
  (e.g. weekdays 8am = `0 8 * * 1-5`).

## 5. Managing
- "what's scheduled?" → `list_schedules`.
- "cancel the morning one" → find it in the list, then `cancel_schedule` by id.
- "move it to 9" → `edit_schedule` with the new time.

Keep confirmations short and human. Don't over-explain cron to the user.
