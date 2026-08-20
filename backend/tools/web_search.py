"""Web search tool for Mai.

Thin wrapper over Agno's DuckDuckGo-backed WebSearchTools with Mai defaults
(few results, region-light). Stateless and always available. Failures inside
the toolkit return a message to the model rather than crashing the turn.
"""

from __future__ import annotations

from agno.tools.websearch import WebSearchTools as _AgnoWebSearchTools


class WebSearchTools(_AgnoWebSearchTools):
    def __init__(self, *, max_results: int = 5) -> None:
        super().__init__(
            enable_search=True,
            enable_news=True,
            fixed_max_results=max_results,
        )
