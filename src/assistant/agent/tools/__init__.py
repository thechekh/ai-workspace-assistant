"""Agent tools: the registry/seam plus the native tool builders.

Re-exported here so callers keep importing from `assistant.agent.tools`
regardless of which module a tool lives in.
"""

from assistant.agent.tools.base import Tool, ToolHandler, ToolRegistry
from assistant.agent.tools.fetch import make_fetch_url, strip_html
from assistant.agent.tools.ingest_repo import make_ingest_repo
from assistant.agent.tools.repo_read import make_repo_read_file
from assistant.agent.tools.search_docs import (
    NO_RELEVANT_DOCS,
    NOTHING_INDEXED,
    make_search_docs,
)

__all__ = [
    "NOTHING_INDEXED",
    "NO_RELEVANT_DOCS",
    "Tool",
    "ToolHandler",
    "ToolRegistry",
    "make_fetch_url",
    "make_ingest_repo",
    "make_repo_read_file",
    "make_search_docs",
    "strip_html",
]
