"""code_exec — Mai's single action. It acts ENTIRELY by writing Python that
composes the mai SDK and runs in a sandbox (the CodeAct pattern)."""

from __future__ import annotations

from agno.tools import Toolkit

from backend.code_exec import mai_sdk
from backend.code_exec.sdk_reference import build_reference
from backend.code_exec.token_store import token_store
from backend.config import settings
from backend.db.tenancy import User
from backend.logging_config import get_logger

logger = get_logger(__name__)


def _has_outbound(code: str) -> bool:
    return any(name in (code or "") for name in mai_sdk.OUTBOUND)


class CodeExecTools(Toolkit):
    def __init__(self, *, user: User, device_id: str, runner, base_url: str,
                 current_chat: str | None = None) -> None:
        self.user = user
        self.device_id = device_id
        self.runner = runner
        self.base_url = base_url
        self.current_chat = current_chat
        super().__init__(
            name="code_exec",
            tools=[self.code_exec],
            instructions=(
                "code_exec(code) runs a Python script using the mai.* SDK in a secure sandbox and "
                "returns whatever you print(). Use it to GATHER and COMPUTE — read the user's chats, "
                "look someone up, search the web, generate or transform images, crunch data. It "
                "returns RESULTS (data, URLs) for YOU to read; it does NOT message the user.\n\n"
                "WORKFLOW: think what the task needs -> write ONE script that does it and print()s a "
                "concise result (e.g. a generated image URL) -> READ the result and ASSESS it -> then "
                "respond to the user: write your reply, and send any image/file with your media tool "
                "(send_image). Sending to the user happens OUTSIDE code_exec, after you've judged the "
                "result. Use real Python — variables, loops, conditionals — chaining mai.* calls.\n\n"
                "RULES:\n"
                "- mai.* runs in Mai's BACKEND (authenticated by your token) and returns real "
                "data — NEVER import replicate/openai/any external API SDK; mai does that for you. "
                "You MAY use numpy, pandas, requests, bs4, lxml, Pillow for your own processing.\n"
                "- 'me'/'my' = the user. Always print() what you need back; don't message from inside "
                "the script.\n"
                "- If a run errors, read the traceback and run a corrected script.\n\n"
                + build_reference()
                + "\n\nEXAMPLES (the SHAPE to use — adapt to ANY task, never copy literally):\n"
                "# summarize a chat\n"
                "code_exec(code='for m in mai.read_chat(\"Sofa\", limit=30): print(m[\"sender\"], "
                "m[\"text\"])')\n"
                "# find groups with over 50 members\n"
                "code_exec(code='print([g[\"name\"] for g in mai.find_chats(kind=\"group\") "
                "if len(mai.get_group_members(g[\"id\"])) > 50])')\n"
                "# cartoon the user's photo: fetch -> transform -> RETURN the url (then send it yourself)\n"
                "code_exec(code='pic = mai.get_profile_picture(\"me\")\\n"
                "art = mai.run_model(\"<an image-to-image model>\", {\"image\": pic})\\n"
                "print(art)')  # then assess the url and send it with your media tool\n\n"
                "code_exec never sends anything — it only fetches/computes and returns data. "
                "Sending messages or media (to the user or anyone) is done with your direct send "
                "tools AFTER you've read and judged the result."
            ),
            add_instructions=True,
        )

    def code_exec(self, code: str, confirmed: bool = False) -> str:
        """Run a Python script (using the mai.* SDK) in a secure sandbox; returns its output.

        Args:
            code: the Python to run. Use print(...) for results.
            confirmed: pass True only after the user approved a script that messages OTHER people.
        """
        from backend.code_exec.guardrails import screen_code

        allowed, reason = screen_code(code)
        if not allowed:
            logger.info("code_exec.blocked", extra={"ctx_user_id": self.user.id,
                                                    "ctx_reason": reason})
            return f"I can't run that — it tripped a safety check ({reason})."
        if _has_outbound(code) and not confirmed:
            return ("CONFIRMATION NEEDED: this script messages other people. Tell the user exactly "
                    "what it will send and to whom, get approval, then call code_exec with "
                    "confirmed=True.")
        token = token_store.mint(self.user.account_id, self.user.id, self.device_id,
                                 outbound_allowed=confirmed, current_chat=self.current_chat)
        res = self.runner.run(code, token=token, base_url=self.base_url,
                              timeout=settings.code_exec_timeout_seconds)
        out = (res.output or "").strip() or ("(no output)" if res.ok else "(failed, no output)")
        out = out[: settings.code_exec_max_result_chars]
        label = "Script output:" if res.ok else "Script error (fix and retry):"
        logger.info("code_exec.run", extra={"ctx_user_id": self.user.id, "ctx_ok": res.ok})
        return f"{label}\n{out}"
