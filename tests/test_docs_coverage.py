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

import importlib.util
import json
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


# --- The code blocks themselves have to be real ----------------------------


def _importable(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def _code_blocks(language: str) -> list[tuple[Path, str]]:
    blocks: list[tuple[Path, str]] = []
    for path in [*REPO_ROOT.glob("docs/**/*.md"), *REPO_ROOT.glob("*.md")]:
        text = path.read_text(encoding="utf-8")
        blocks += [
            (path, match.group(1))
            for match in re.finditer(rf"^```{language}\n(.*?)^```", text, re.S | re.M)
        ]
    return blocks


def test_json_blocks_in_docs_parse() -> None:
    """A config snippet you cannot paste is worse than no snippet."""
    broken = []
    for path, block in _code_blocks("json"):
        try:
            json.loads(block)
        except ValueError as exc:
            broken.append(f"{path.name}: {exc}")
    assert not broken, "invalid JSON in documentation:\n  " + "\n  ".join(broken)


def test_python_blocks_in_docs_parse() -> None:
    """Illustrative snippets may reference undefined names, but must be valid
    Python — a signature quoted without its body silently stops matching the
    source it claims to show."""
    broken = []
    for path, block in _code_blocks("python"):
        try:
            compile(block, str(path), "exec")
        except SyntaxError as exc:
            broken.append(f"{path.name} line {exc.lineno}: {exc.msg}")
    assert not broken, "unparseable Python in documentation:\n  " + "\n  ".join(broken)


def test_commands_the_docs_tell_you_to_run_exist() -> None:
    """Every script, module and npm script named in a shell block must resolve.

    This is what makes the handbook followable: a reader copying a command
    should never hit "no such file". Renaming a script now fails the build
    instead of quietly breaking the getting-started page.
    """
    missing: list[str] = []
    npm_scripts = json.loads((REPO_ROOT / "frontend/package.json").read_text(encoding="utf-8"))[
        "scripts"
    ]

    for path, block in _code_blocks("sh"):
        for raw in block.splitlines():
            line = raw.split("#")[0].strip()

            for script in re.findall(r"(?:uv run )?python3? ([\w/\.-]+\.py)", line):
                if not (REPO_ROOT / script).exists():
                    missing.append(f"{path.name}: script {script}")

            for module in re.findall(r"python3? -m ([\w.]+)", line):
                stem = Path(module.replace(".", "/"))
                on_disk = any(
                    candidate.exists()
                    for candidate in (
                        REPO_ROOT / stem.with_suffix(".py"),
                        REPO_ROOT / "src" / stem.with_suffix(".py"),
                        REPO_ROOT / "src" / stem / "__init__.py",
                        REPO_ROOT / stem / "__init__.py",
                    )
                )
                # `python -m json.tool` is as legitimate as our own modules, so
                # fall back to "is it importable at all" for stdlib and
                # installed packages before calling it missing.
                if not on_disk and not _importable(module):
                    missing.append(f"{path.name}: module {module}")

            for script in re.findall(r"npm run ([\w:-]+)", line):
                if script not in npm_scripts:
                    missing.append(f"{path.name}: npm run {script}")

    assert not missing, "documented commands that do not exist:\n  " + "\n  ".join(
        sorted(set(missing))
    )


def test_line_anchors_in_docs_point_at_real_code() -> None:
    """`file.py#L42` links must land on the line they claim.

    The walkthrough is only useful if a reader following it lands on the code
    being described. A plain link check cannot catch this: the file still
    exists after a refactor moves the function forty lines down.

    The rule is deliberately "inside the file, and on a line with code on it"
    rather than "on a `def`" — pointing at a specific statement (the line that
    reads the next frame, say) is legitimate and often the clearest anchor.
    What this catches is the real failure: a file shrinking below the anchor,
    or the anchor sliding into blank space or a closing bracket.
    """
    stale: list[str] = []
    for doc in [*REPO_ROOT.glob("docs/**/*.md"), *REPO_ROOT.glob("*.md")]:
        text = doc.read_text(encoding="utf-8")
        for match in re.finditer(r"\]\(([\w./-]+\.py)#L(\d+)\)", text):
            target = (doc.parent / match.group(1)).resolve()
            line = int(match.group(2))
            if not target.exists():
                stale.append(f"{doc.name}: missing {match.group(1)}")
                continue
            lines = target.read_text(encoding="utf-8").splitlines()
            if line > len(lines):
                stale.append(
                    f"{doc.name}: {match.group(1)}#L{line} but the file has {len(lines)} lines"
                )
                continue
            body = lines[line - 1].strip()
            if not body or body in {")", "]", "}", '"""'}:
                stale.append(f"{doc.name}: {match.group(1)}#L{line} landed on blank/punctuation")

    assert not stale, "documentation line anchors have drifted from the code:\n  " + "\n  ".join(
        stale
    )


def test_the_learning_roadmap_covers_every_source_file() -> None:
    """The roadmap promises "no gaps" — hold it to that.

    A study plan that silently stops covering new code is worse than none: a
    reader finishes it believing they have seen everything. Adding a module
    without slotting it into a session now fails the build.
    """
    roadmap = (REPO_ROOT / "docs/project/learning-roadmap.md").read_text(encoding="utf-8")
    sources = sorted(
        path.name
        for path in (REPO_ROOT / "src/assistant").rglob("*.py")
        if path.name != "__init__.py"
    )
    uncovered = [name for name in sources if name not in roadmap]
    assert not uncovered, (
        "source files the learning roadmap never names:\n  "
        + "\n  ".join(uncovered)
        + "\n(add them to a session and to the coverage checklist)"
    )

    # The headline count must match reality too, since the page claims it.
    claimed = re.search(r"\*\*(\d+) source files\.", roadmap)
    assert claimed, "the roadmap should state how many source files it covers"
    assert int(claimed.group(1)) == len(sources), (
        f"roadmap claims {claimed.group(1)} source files; there are {len(sources)}"
    )


def test_the_reading_roadmap_places_every_document() -> None:
    """docs/README.md sequences all 42 documents — hold it to that.

    The roadmap's promise is "read top to bottom and you have read every
    document". A new page that never gets slotted in breaks that silently: the
    reader finishes believing they are done. This also checks the numbering is
    contiguous, since a duplicated or skipped number makes the list unusable
    as a checklist.
    """
    index = (REPO_ROOT / "docs/README.md").read_text(encoding="utf-8")
    start = index.index("### The reading roadmap")
    section = index[start : index.index("**Presenting it?**", start)]

    unplaced: list[str] = []
    for path in sorted((REPO_ROOT / "docs").rglob("*.md")):
        link = path.relative_to(REPO_ROOT / "docs").as_posix()
        if f"]({link})" not in section:
            unplaced.append(f"docs/{link}")
    for path in sorted(REPO_ROOT.glob("*.md")):
        if f"](../{path.name})" not in section:
            unplaced.append(path.name)

    assert not unplaced, (
        "documents missing from the reading roadmap in docs/README.md:\n  " + "\n  ".join(unplaced)
    )

    numbers = [int(n) for n in re.findall(r"^\| (\d+) \|", section, re.M)]
    assert numbers == list(range(1, len(numbers) + 1)), (
        f"reading-roadmap numbering is not contiguous 1..{len(numbers)}: {numbers}"
    )
