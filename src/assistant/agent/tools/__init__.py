"""Agent tools: the registry/seam plus the native tool builders.

Re-exported here so callers keep importing from `assistant.agent.tools`
regardless of which module a tool lives in.
"""

from assistant.agent.tools.base import Tool, ToolHandler, ToolRegistry
from assistant.agent.tools.fetch import make_fetch_url, strip_html
from assistant.agent.tools.search_docs import NO_RELEVANT_DOCS, make_search_docs

__all__ = [
    "NO_RELEVANT_DOCS",
    "Tool",
    "ToolHandler",
    "ToolRegistry",
    "make_fetch_url",
    "make_search_docs",
    "strip_html",
]
