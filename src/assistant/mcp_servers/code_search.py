"""Custom MCP server: code search over a local repository.

Pure-python search (no ripgrep dependency) rooted at CODE_SEARCH_ROOT
(default: the working directory). Zero credentials, works offline.

Run standalone over stdio:
    python -m assistant.mcp_servers.code_search
"""

import os
import re
from collections.abc import Iterator
from pathlib import Path

from mcp.server.mcpserver import MCPServer

mcp = MCPServer("code-search")

ROOT = Path(os.environ.get("CODE_SEARCH_ROOT", ".")).resolve()

_IGNORED_DIRS = {"node_modules", "__pycache__", "dist", "build"}
_TEXT_EXTENSIONS = {
    ".py", ".ts", ".tsx", ".js", ".vue", ".md", ".toml", ".yaml", ".yml",
    ".json", ".html", ".css", ".txt", ".cfg", ".ini", ".sql",
}  # fmt: skip
_MAX_FILE_BYTES = 512_000


def _iter_files() -> Iterator[Path]:
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in _IGNORED_DIRS and not d.startswith(".")]
        for filename in filenames:
            path = Path(dirpath) / filename
            if path.suffix.lower() in _TEXT_EXTENSIONS and path.stat().st_size < _MAX_FILE_BYTES:
                yield path


@mcp.tool()
def search_code(pattern: str, max_results: int = 20) -> str:
    """Search the repository for lines matching a regex pattern (case-insensitive).

    Returns `path:line: content` matches, one per line.
    """
    try:
        regex = re.compile(pattern, re.IGNORECASE)
    except re.error as exc:
        return f"error: invalid regex {pattern!r}: {exc}"

    hits: list[str] = []
    for path in _iter_files():
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        relative = path.relative_to(ROOT).as_posix()
        for line_number, line in enumerate(text.splitlines(), start=1):
            if regex.search(line):
                hits.append(f"{relative}:{line_number}: {line.strip()[:200]}")
                if len(hits) >= max_results:
                    return "\n".join(hits)
    return "\n".join(hits) if hits else f"no matches for {pattern!r}"


@mcp.tool()
def read_file(path: str, start_line: int = 1, max_lines: int = 100) -> str:
    """Read a file from the repository (path relative to the repo root)."""
    target = (ROOT / path).resolve()
    if not target.is_relative_to(ROOT):
        return "error: path escapes the repository root"
    if not target.is_file():
        return f"error: no such file: {path}"
    lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    start = max(start_line, 1)
    section = lines[start - 1 : start - 1 + max_lines]
    if not section:
        return f"(no lines at {start_line}+ — the file has {len(lines)} lines)"
    return "\n".join(f"{number}: {line}" for number, line in enumerate(section, start=start))


if __name__ == "__main__":
    mcp.run()  # stdio transport
