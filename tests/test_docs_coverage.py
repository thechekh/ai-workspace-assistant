"""The documentation must keep up with what the code exposes.

`test_docs_links.py` proves the documents hang together and
`test_docs_consistency.py` proves the numbers in them are true. Neither
notices a *new* thing that nobody wrote about — a setting added without a row
in the configuration table, an endpoint with no page describing it, a metric
that never reaches the observability chapter.

This is the third leg: everything the code offers a reader — settings,
endpoints, metrics, tools, wire frames, dependencies — has to be mentioned
somewhere in `docs/` or a root document. Mentioned is a low bar deliberately;
the point is that adding a feature and forgetting the docs fails the build
instead of going unnoticed for months.
"""

import re
import tomllib
from pathlib import Path

import pytest

from assistant.api.schemas import TurnSummary
from assistant.config import Settings

REPO_ROOT = Path(__file__).resolve().parent.parent


def _all_prose() -> str:
    """Every documentation file, lowercased — matching is case-insensitive."""
    files = [*REPO_ROOT.glob("docs/**/*.md"), *REPO_ROOT.glob("*.md")]
    return "\n".join(path.read_text(encoding="utf-8") for path in files).lower()


DOCS = _all_prose()


def _mentioned(*candidates: str) -> bool:
    return any(candidate.lower() in DOCS for candidate in candidates)


def test_every_setting_is_documented() -> None:
    """A knob nobody documented is a knob nobody will find."""
    undocumented = [
        name
        for name in sorted(Settings.model_fields)
        # Either as the env var or as the bare field name: the configuration
        # tables state the ASSISTANT_ prefix once in their header.
        if not _mentioned(f"assistant_{name}", f"`{name}`", f"| `{name.upper()}`")
    ]
    assert not undocumented, (
        "settings with no mention in any document:\n  "
        + "\n  ".join(undocumented)
        + "\n(add a row to docs/handbook/02-getting-started.md and .env.example)"
    )


def test_every_endpoint_is_documented() -> None:
    routes = (REPO_ROOT / "src/assistant/api/routes.py").read_text(encoding="utf-8")
    paths = [
        f"/api{path}"
        for _, path in re.findall(r'@router\.(get|post|delete)\(\s*\n?\s*"([^"]+)"', routes)
    ]
    # Served outside the API router.
    paths += ["/chat", "/healthz", "/metrics", "/dev"]

    undocumented = [
        path for path in sorted(set(paths)) if not _mentioned(path.split("{")[0].rstrip("/"))
    ]
    assert not undocumented, "endpoints no document mentions:\n  " + "\n  ".join(undocumented)


def test_every_metric_is_documented() -> None:
    """The observability chapter's table is the only place these are listed."""
    telemetry = (REPO_ROOT / "src/assistant/telemetry.py").read_text(encoding="utf-8")
    metrics = sorted(set(re.findall(r'"(assistant_[a-z_]+)"', telemetry)))
    assert metrics, "no metrics found — has telemetry.py moved?"

    undocumented = [metric for metric in metrics if not _mentioned(metric)]
    assert not undocumented, (
        "metrics missing from docs/handbook/07-observability.md:\n  " + "\n  ".join(undocumented)
    )


def test_every_wire_frame_is_documented() -> None:
    """The protocol table in chapter 08 is the contract a client reads."""
    sources = [
        (REPO_ROOT / "src/assistant/api/schemas.py").read_text(encoding="utf-8"),
        (REPO_ROOT / "src/assistant/agent/base.py").read_text(encoding="utf-8"),
    ]
    frames = sorted(
        {m for text in sources for m in re.findall(r'type:\s*Literal\["(\w+)"\]', text)}
    )
    assert frames, "no frame types found — have the schemas moved?"

    undocumented = [frame for frame in frames if not _mentioned(f"`{frame}`", f'"{frame}"')]
    assert not undocumented, (
        "WS frames missing from docs/handbook/08-agents-memory-ws.md:\n  "
        + "\n  ".join(undocumented)
    )


def test_every_turn_summary_field_is_documented() -> None:
    """The stats line is the UI's contract; each field should be explained."""
    undocumented = [
        name
        for name in sorted(TurnSummary.model_fields)
        if name != "type" and not _mentioned(f"`{name}`", name.replace("_", " "))
    ]
    assert not undocumented, "turn-summary fields nobody documented:\n  " + "\n  ".join(
        undocumented
    )


def test_every_agent_tool_is_documented() -> None:
    """Native tools plus the bundled MCP servers' tools."""
    tools = [
        "search_docs",
        "fetch_url",
        "code__search_code",
        "code__read_file",
        "github__list_pull_requests",
        "github__get_pull_request",
        "github__list_issues",
    ]
    undocumented = [tool for tool in tools if not _mentioned(tool)]
    assert not undocumented, "tools missing from docs/reference/tools.md:\n  " + "\n  ".join(
        undocumented
    )


def test_every_source_module_is_referenced() -> None:
    """Someone reading the docs should be able to find every file."""
    modules = sorted(
        path.name for path in REPO_ROOT.glob("src/assistant/**/*.py") if path.name != "__init__.py"
    )
    unreferenced = [module for module in modules if not _mentioned(module)]
    assert not unreferenced, "source files no document points at:\n  " + "\n  ".join(unreferenced)


def test_every_runtime_dependency_is_named() -> None:
    """ "Which technologies is this built on?" must be answerable from the docs."""
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    names = sorted(
        re.split(r"[><=\[;\s]", spec)[0] for spec in pyproject["project"]["dependencies"]
    )
    undocumented = [name for name in names if not _mentioned(name)]
    assert not undocumented, (
        "dependencies missing from docs/handbook/03-technologies.md:\n  "
        + "\n  ".join(undocumented)
    )


@pytest.mark.parametrize(
    "command",
    [
        "uv sync",
        "uv run pytest",
        "uv run ruff check",
        "uv run pyright",
        "uv run uvicorn assistant.main:app",
        "docker compose up",
        "npm run dev",
        "npm run build",
        "uv run taskiq worker",
        "python -m assistant.rag.ingest",
        "evals/run_retrieval.py",
        "pre-commit",
    ],
)
def test_every_command_a_reader_needs_appears(command: str) -> None:
    """How to run it: install, serve, test, lint, ingest, evaluate, background."""
    assert _mentioned(command), f"no document shows how to run `{command}`"
