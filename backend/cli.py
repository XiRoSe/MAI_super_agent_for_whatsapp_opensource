"""Local CLI harness — drive Mai's brain (agent + memory + tools) without WhatsApp.

Run:  mai            (or)  python -m mai.cli
Useful in-chat commands:
  /tz <IANA tz>     set your timezone (needed before scheduling)
  /user <number>    switch to a different test tenant (prove isolation)
  /whoami           show the current tenant
  /quit             exit
"""

from __future__ import annotations

import sys

from backend.agent.mai_agent import respond
from backend.config import settings
from backend.db.base import SessionLocal, init_db
from backend.logging_config import configure_logging, new_trace_id
from backend.provisioning import get_or_create_user, set_user_timezone
from backend.tools.registry import build_tools

DEFAULT_NUMBER = "+15551230000"


def _print(role: str, text: str) -> None:
    print(f"\n{role}: {text}\n")


def generate_cli_reply(session, user, text: str) -> str:
    """One message -> one reply. The unit a full CLI turn exercises (tests use this)."""
    new_trace_id("cli")
    tools = build_tools(user, session)
    return respond(user, text, tools=tools, session=session)


def run_repl(initial_number: str = DEFAULT_NUMBER) -> None:
    configure_logging()
    init_db()
    number = initial_number
    session = SessionLocal()
    user, created = get_or_create_user(session, whatsapp_number=number, display_name="You")
    print("Mai CLI harness — type a message, or /quit to exit. (/help for commands)")
    print(f"Tenant: account={user.account_id} user={user.id} number={number}"
          f" tz={user.timezone or 'unset'}")

    while True:
        try:
            text = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue

        if text in ("/quit", "/exit"):
            break
        if text == "/help":
            print("  /tz <tz>  /user <number>  /whoami  /quit")
            continue
        if text == "/whoami":
            print(f"  account={user.account_id} user={user.id} number={user.whatsapp_number}"
                  f" tz={user.timezone or 'unset'}")
            continue
        if text.startswith("/tz "):
            tz = text[4:].strip()
            set_user_timezone(session, user, tz)
            print(f"  timezone set to {tz}")
            continue
        if text.startswith("/user "):
            number = text[6:].strip()
            user, created = get_or_create_user(session, whatsapp_number=number, display_name="You")
            print(f"  switched to account={user.account_id} user={user.id} number={number}")
            continue

        reply = generate_cli_reply(session, user, text)
        _print("mai", reply)

    session.close()


def main() -> None:
    number = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_NUMBER
    if settings.model_provider == "openrouter" and not settings.openrouter_api_key:
        print(
            "NOTE: OPENROUTER_API_KEY is not set. The agent will return a graceful "
            "error on each turn until you set a model key in .env.\n",
            file=sys.stderr,
        )
    run_repl(number)


if __name__ == "__main__":
    main()
