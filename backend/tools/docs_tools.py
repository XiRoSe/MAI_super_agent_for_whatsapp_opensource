"""Docs tools — Mai's per-user knowledge base (markdown documents in Drive).

Documents are for durable, titled content the user expects back verbatim (notes,
lists, briefs) — distinct from Agno memory, which holds small conversational
facts. Storage is the user's own Google Drive via the `drive.file` scope, so the
token can only touch the folder this app created; the OAuth token itself never
leaves the server (typed tools, not CodeAct).
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from agno.tools import Toolkit

from backend import gdrive, oauth_google
from backend.db.scoped import TenantScope
from backend.db.tenancy import User


class DocsTools(Toolkit):
    def __init__(self, *, user: User, session: Session):
        self.user = user
        self.scope = TenantScope(session, account_id=user.account_id, user_id=user.id)
        super().__init__(
            name="docs",
            tools=[
                self.list_documents,
                self.read_document,
                self.save_document,
                self.delete_document,
            ],
            instructions=(
                "The knowledge base holds the user's DOCUMENTS — titled markdown notes "
                "they expect back verbatim. Use it for anything the user asks to save, "
                "keep, or write down as a document/note/list; keep using memory for "
                "small facts. save_document OVERWRITES: to change an existing document, "
                "read_document first, apply the edit to the full text, then save the "
                "whole result. If a tool answers that Drive isn't connected, relay its "
                "connect link to the user as-is."
            ),
            add_instructions=True,
        )

    def _not_connected(self) -> str:
        if not oauth_google.is_configured():
            return ("The knowledge base isn't available yet — Google Drive access is "
                    "not configured on the server.")
        return ("Your knowledge base isn't connected yet. Send the user this link to "
                f"connect their Google Drive: {oauth_google.connect_url(self.user)} — "
                "Mai only gets a single folder it creates, nothing else in their Drive.")

    def list_documents(self) -> str:
        """List the documents in the user's knowledge base."""
        try:
            docs = gdrive.list_docs(self.scope)
        except gdrive.DriveNotConnected:
            return self._not_connected()
        if not docs:
            return "The knowledge base is empty — no documents saved yet."
        return "Documents:\n" + "\n".join(
            f"- {d.title}" + (f" (updated {d.modified})" if d.modified else "") for d in docs
        )

    def read_document(self, title: str) -> str:
        """Return a document's full text.

        Args:
            title: the document's title as shown by list_documents.
        """
        try:
            body = gdrive.read_doc(self.scope, title)
        except gdrive.DriveNotConnected:
            return self._not_connected()
        if body is None:
            return f"No document titled '{title}'. Use list_documents to see what exists."
        return body

    def save_document(self, title: str, body: str) -> str:
        """Create a document, or OVERWRITE an existing one with the full new text.

        Args:
            title: short document title, e.g. "reading list".
            body: the COMPLETE markdown content to store (not a diff).
        """
        try:
            doc = gdrive.save_doc(self.scope, title, body)
        except gdrive.DriveNotConnected:
            return self._not_connected()
        return f"Saved '{doc.title}' to the knowledge base."

    def delete_document(self, title: str) -> str:
        """Delete a document from the knowledge base.

        Args:
            title: the document's title as shown by list_documents.
        """
        try:
            deleted = gdrive.delete_doc(self.scope, title)
        except gdrive.DriveNotConnected:
            return self._not_connected()
        return (f"Deleted '{title}'." if deleted
                else f"No document titled '{title}' — nothing deleted.")
