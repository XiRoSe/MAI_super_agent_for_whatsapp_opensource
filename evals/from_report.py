"""Turn a user-approved ErrorReport into a runnable eval skeleton.

Reads the report's deterministic capture and emits a paste-ready `Scenario` (surface="turn")
or `ExecutionScenario` (surface="schedule") for `evals/scenarios.py`. The reproducible inputs
— command / chat shape / schedule / the tools that fired — are pre-filled; the `stubs` and the
grader (what SHOULD have happened) are left as TODO, seeded by Mai's problem/expected notes.

Self-contained: it does NOT import the harness/scenarios modules, so it runs even where the
eval deps aren't installed; it only prints text you paste in.

Usage:
    python -m evals.from_report <report_id>          # read from the Mai DB
    python -m evals.from_report --json report.json   # read an exported report dict
"""

from __future__ import annotations

import argparse
import json
import re
import sys


def _slug(text: str) -> str:
    parts = [w for w in re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).split("_") if w]
    return "_".join(parts)[:40] or "captured"


def _tool_names(tool_calls: list[dict]) -> list[str]:
    seen: list[str] = []
    for t in tool_calls or []:
        n = (t or {}).get("name")
        if n and n not in seen:
            seen.append(n)
    return seen


def _transcript_comment(transcript: list[dict]) -> list[str]:
    if not transcript:
        return ["# (no transcript captured)"]
    out = ["# --- captured conversation (context) ---"]
    for m in transcript:
        who = "Mai" if (m or {}).get("role") == "assistant" else "User"
        text = (m or {}).get("text", "").replace("\n", " ")
        out.append(f"#   [{who}] {text}")
    return out


def render(r: dict) -> str:
    surface = r.get("surface") or "turn"
    tools = _tool_names(r.get("tool_calls") or [])
    slug = _slug(r.get("command") or r.get("summary") or "captured")

    head = [
        f"# === eval generated from ErrorReport #{r.get('id')} "
        f"(rev {r.get('git_rev') or '?'}, {r.get('created_at', '')}) ===",
        f"# Problem (Mai):   {r.get('summary') or '(n/a)'}",
        f"# Expected (Mai):  {r.get('mai_expected') or '(n/a)'}",
        f"# Notes (Mai):     {r.get('mai_notes') or '(n/a)'}",
        *_transcript_comment(r.get("transcript") or []),
        "#",
        "# TODO(1): fill `stubs` with a canned return per mai.* call (keeps the run hermetic).",
        "# TODO(2): set the grader to assert the CORRECT outcome (what SHOULD have happened).",
    ]

    if surface == "schedule" and r.get("schedule"):
        s = r["schedule"]
        var = "_REPORT_SCHED_" + slug.upper()
        body = [
            f"{var} = ExecutionScenario(",
            f"    name={('report_sched_' + slug)!r},",
            f"    action_text={s.get('action_text', '')!r},",
            f"    cron={(s.get('cron') or '0 8 * * *')!r},",
            f"    kind={(s.get('kind') or 'dynamic')!r},",
            "    output_check=lambda msg: False,  # TODO(2): assert the delivered message is correct",
            ")",
            "",
            f"# Add {var} to EXECUTION_SCENARIOS in evals/scenarios.py.",
        ]
        return "\n".join(head + [""] + body)

    # surface == "turn"
    chat_type = r.get("chat_type") or "self_dm"
    if chat_type == "self_dm":
        shape = "    is_self_dm=True,"
    else:
        number = r.get("contact_number") or ""
        jid = r.get("chat_jid") or (f"{number}@s.whatsapp.net" if number else "")
        shape = (f"    contact_name={(r.get('contact_name') or '')!r},\n"
                 f"    contact_number={number!r},\n"
                 f"    contact_jid={jid!r},")
    var = "_REPORT_" + slug.upper()
    body = [
        f"{var} = Scenario(",
        f"    name={('report_' + slug)!r},",
        f"    command={r.get('command', '')!r},",
        shape,
        f"    expected_tool_calls={tools!r},  # captured from the turn — EDIT to the CORRECT set",
        "    expected_sdk_calls=[],  # TODO: mai.* calls the turn should make, if any",
        "    stubs={},               # TODO(1)",
        "    # state_check=...,      # TODO(2): grade real persisted rows where applicable",
        ")",
        "",
        f"# Add {var} to SCENARIOS in evals/scenarios.py.",
    ]
    return "\n".join(head + [""] + body)


def _load_from_db(report_id: int) -> dict:
    from backend.db.base import SessionLocal
    from backend.db.tenancy import ErrorReport

    def _loads(blob, default):
        try:
            return json.loads(blob) if blob else default
        except Exception:
            return default

    session = SessionLocal()
    try:
        r = session.get(ErrorReport, report_id)
        if r is None:
            sys.exit(f"No ErrorReport #{report_id} found.")
        return {
            "id": r.id,
            "command": r.command,
            "summary": r.summary,
            "surface": r.surface,
            "chat_type": r.chat_type,
            "contact_name": r.contact_name,
            "contact_number": r.contact_number,
            "chat_jid": r.chat_jid,
            "mai_expected": r.mai_expected,
            "mai_notes": r.mai_notes,
            "transcript": _loads(r.transcript_json, []),
            "tool_calls": _loads(r.tool_calls_json, []),
            "schedule": _loads(r.schedule_json, None),
            "git_rev": r.git_rev,
            "created_at": r.created_at.isoformat() if r.created_at else "",
        }
    finally:
        session.close()


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(
        description="Render an eval Scenario/ExecutionScenario skeleton from a Mai ErrorReport."
    )
    ap.add_argument("report_id", nargs="?", type=int, help="ErrorReport id (reads the Mai DB)")
    ap.add_argument("--json", dest="json_path", help="path to an exported report JSON instead")
    args = ap.parse_args(argv)

    if args.json_path:
        with open(args.json_path) as fh:
            report = json.load(fh)
    elif args.report_id is not None:
        report = _load_from_db(args.report_id)
    else:
        ap.error("give a report_id or --json <path>")

    print(render(report))


if __name__ == "__main__":
    main()
