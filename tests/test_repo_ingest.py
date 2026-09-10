"""The `ingest_repo` agent tool — GitHub docs straight into the knowledge base.

All GitHub traffic is served by `MockHTTP` (conftest) over an httpx2 mock
transport, injected through the same `client=` seam the app uses in
production: the suite stays offline, and the routes record what actually went
over the wire (auth header, raw-content Accept). The namespacing test is the
regression guard for a real incident — a flat-basename upload let a second
project's README.md silently replace the first's.
"""

import httpx2
import pytest
from qdrant_client import AsyncQdrantClient

from assistant.agent.tools.ingest_repo import make_ingest_repo
from assistant.rag.repo import MAX_FILE_BYTES, RepoIngestError, fetch_repo_documents
from assistant.rag.store import VectorStore
from tests.conftest import HermeticSettings, MockHTTP

API = "https://api.github.com"


def _empty_store() -> VectorStore:
    return VectorStore(AsyncQdrantClient(":memory:"), "test_docs")


def _mock_repo(http: MockHTTP, repo: str = "acme/handbook") -> None:
    """A two-file repository: docs/guide.md + README.md, default branch `main`."""
    http.get(f"{API}/repos/{repo}", json={"default_branch": "main"})
    http.get(
        f"{API}/repos/{repo}/git/trees/main?recursive=1",
        json={
            "truncated": False,
            "tree": [
                {"type": "blob", "path": "README.md", "size": 120},
                {"type": "blob", "path": "docs/guide.md", "size": 300},
                {"type": "blob", "path": "src/app.py", "size": 999},
                {"type": "tree", "path": "docs"},
            ],
        },
    )
    http.get(
        f"{API}/repos/{repo}/contents/README.md?ref=main",
        text="# Handbook\n\nWelcome to the handbook repo.",
    )
    http.get(
        f"{API}/repos/{repo}/contents/docs/guide.md?ref=main",
        text="# Guide\n\nInvoices are generated nightly.",
    )


async def test_the_tool_ingests_with_namespaced_sources() -> None:
    store = _empty_store()
    http = MockHTTP()
    _mock_repo(http)
    async with http.client() as client:
        tool = make_ingest_repo(HermeticSettings(), store, client=client)
        result = await tool.handler({"repo": "acme/handbook"})

    assert not result.startswith("error:"), result
    # The result is what the MODEL reads — it must name the sources so the
    # next answer can cite them.
    assert "acme/handbook/README.md" in result
    assert "acme/handbook/docs/guide.md" in result
    sources = {source for source, _ in await store.list_sources()}
    # owner/repo/path — the namespace that makes collisions impossible.
    assert sources == {"acme/handbook/README.md", "acme/handbook/docs/guide.md"}


async def test_two_repos_with_the_same_filenames_do_not_collide() -> None:
    """The incident that motivated namespacing, reproduced end to end."""
    store = _empty_store()
    http = MockHTTP()
    _mock_repo(http, "acme/handbook")
    _mock_repo(http, "acme/other")
    async with http.client() as client:
        tool = make_ingest_repo(HermeticSettings(), store, client=client)
        assert not (await tool.handler({"repo": "acme/handbook"})).startswith("error:")
        assert not (await tool.handler({"repo": "acme/other"})).startswith("error:")

    sources = {source for source, _ in await store.list_sources()}
    assert "acme/handbook/README.md" in sources
    assert "acme/other/README.md" in sources  # both survive


async def test_a_missing_repo_becomes_an_actionable_error_result() -> None:
    """A tool crash must reach the model as text it can relay, never a raise."""
    http = MockHTTP()
    http.get(f"{API}/repos/acme/ghost", status=404)
    async with http.client() as client:
        tool = make_ingest_repo(HermeticSettings(), _empty_store(), client=client)
        result = await tool.handler({"repo": "acme/ghost"})
    assert result.startswith("error:")
    assert "ASSISTANT_GITHUB_TOKEN" in result


async def test_a_repo_with_no_documentation_is_an_error_not_an_empty_success() -> None:
    http = MockHTTP()
    http.get(f"{API}/repos/acme/code-only", json={"default_branch": "main"})
    http.get(
        f"{API}/repos/acme/code-only/git/trees/main?recursive=1",
        json={"tree": [{"type": "blob", "path": "src/app.py", "size": 10}]},
    )
    async with http.client() as client:
        tool = make_ingest_repo(HermeticSettings(), _empty_store(), client=client)
        result = await tool.handler({"repo": "acme/code-only"})
    assert result.startswith("error:")
    assert "no .md/.txt/.rst" in result


async def test_a_missing_repo_argument_is_reported_without_any_request() -> None:
    http = MockHTTP()  # no routes: any request at all shows up as unmatched
    async with http.client() as client:
        tool = make_ingest_repo(HermeticSettings(), _empty_store(), client=client)
        result = await tool.handler({})
    assert result.startswith("error:")
    assert "repo" in result
    assert http.unmatched == []


@pytest.mark.parametrize("bad", ["not-a-repo", "owner/", "/repo", "a/b/c", "owner/re po", "../up"])
async def test_malformed_repo_names_are_rejected_before_any_request(bad: str) -> None:
    http = MockHTTP()
    async with http.client() as client:
        with pytest.raises(RepoIngestError) as exc:
            await fetch_repo_documents(bad, client=client)
    assert "not an owner/repository name" in exc.value.detail
    assert http.unmatched == [], "the name is validated before anything is sent"


async def test_the_github_token_reaches_the_wire_and_traversal_paths_do_not_land() -> None:
    http = MockHTTP()
    meta = http.get(f"{API}/repos/acme/handbook", json={"default_branch": "main"})
    http.get(
        f"{API}/repos/acme/handbook/git/trees/main?recursive=1",
        json={
            "tree": [
                {"type": "blob", "path": "ok.md", "size": 10},
                # Hostile listing: the tree is external data.
                {"type": "blob", "path": "../escape.md", "size": 10},
                {"type": "blob", "path": "docs/../../up.md", "size": 10},
                {"type": "blob", "path": "big.md", "size": MAX_FILE_BYTES + 1},
            ]
        },
    )
    http.get(f"{API}/repos/acme/handbook/contents/ok.md?ref=main", text="# ok")
    async with http.client() as client:
        documents, skipped = await fetch_repo_documents(
            "acme/handbook", client=client, token="ghp_test"
        )

    assert meta.last.headers["Authorization"] == "Bearer ghp_test"
    assert [source for source, _ in documents] == ["acme/handbook/ok.md"]
    assert any("unsafe path" in item for item in skipped)
    assert any("larger than" in item for item in skipped)


async def test_the_file_cap_bounds_an_accidental_monorepo() -> None:
    tree = [{"type": "blob", "path": f"docs/{i}.md", "size": 5} for i in range(5)]
    http = MockHTTP()
    http.get(f"{API}/repos/acme/mono", json={"default_branch": "main"})
    http.get(f"{API}/repos/acme/mono/git/trees/main?recursive=1", json={"tree": tree})
    http.get(rf"{API}/repos/acme/mono/contents/.*", text="# doc", regex=True)
    async with http.client() as client:
        documents, skipped = await fetch_repo_documents("acme/mono", client=client, max_files=2)

    assert len(documents) == 2
    assert sum("over the 2-file limit" in item for item in skipped) == 3


async def test_include_code_indexes_source_files_and_skips_the_bulk() -> None:
    """include_code widens to source files without dragging in lockfiles/deps."""
    http = MockHTTP()
    http.get(f"{API}/repos/acme/svc", json={"default_branch": "main"})
    http.get(
        f"{API}/repos/acme/svc/git/trees/main?recursive=1",
        json={
            "tree": [
                {"type": "blob", "path": "README.md", "size": 20},
                {"type": "blob", "path": "src/payments/adapter.py", "size": 900},
                {"type": "blob", "path": "frontend/app.min.js", "size": 900},
                {"type": "blob", "path": "node_modules/x/index.js", "size": 900},
                {"type": "blob", "path": "uv.lock", "size": 900},
                {"type": "blob", "path": "image.png", "size": 900},
            ]
        },
    )
    http.get(rf"{API}/repos/acme/svc/contents/.*", text="class PaymentAdapter: ...", regex=True)
    async with http.client() as client:
        with_code, _ = await fetch_repo_documents("acme/svc", client=client, include_code=True)
        docs_only, _ = await fetch_repo_documents("acme/svc", client=client)

    assert [s for s, _ in with_code] == ["acme/svc/README.md", "acme/svc/src/payments/adapter.py"]
    assert [s for s, _ in docs_only] == ["acme/svc/README.md"]


async def test_repo_read_file_fetches_one_public_file_without_a_token() -> None:
    """The no-PAT story: a public repo's file arrives with no Authorization header."""
    from assistant.agent.tools.repo_read import make_repo_read_file

    http = MockHTTP()
    route = http.get(
        f"{API}/repos/acme/svc/contents/src/payments/adapter.py",
        text="class PaymentAdapter:\n    pass\n",
    )
    async with http.client() as client:
        tool = make_repo_read_file(HermeticSettings(), client=client)  # github_token unset
        result = await tool.handler({"repo": "acme/svc", "path": "src/payments/adapter.py"})

    assert "Authorization" not in route.last.headers
    assert route.last.headers["Accept"] == "application/vnd.github.raw+json"
    assert result.startswith("// acme/svc/src/payments/adapter.py")
    assert "class PaymentAdapter" in result


async def test_repo_read_file_refuses_traversal_and_reports_missing_files() -> None:
    from assistant.agent.tools.repo_read import make_repo_read_file

    http = MockHTTP()
    http.get(f"{API}/repos/acme/svc/contents/gone.py", status=404)
    async with http.client() as client:
        tool = make_repo_read_file(HermeticSettings(), client=client)
        assert (await tool.handler({"repo": "acme/svc", "path": "../secrets"})).startswith("error:")
        assert (await tool.handler({"repo": "acme/svc"})).startswith("error:")
        assert http.unmatched == [], "both are rejected before anything is sent"

        result = await tool.handler({"repo": "acme/svc", "path": "gone.py"})
    assert result.startswith("error:")
    assert "case matters" in result


async def test_an_unrouted_call_is_visible_rather_than_silent() -> None:
    """The mock answers 404 for anything unrouted and records it, so a test
    that would have reached the internet fails on its assertions instead of
    passing quietly."""
    http = MockHTTP()
    async with http.client() as client:
        response = await client.get("https://example.invalid/whatever")
    assert response.status_code == 404
    assert [str(request.url) for request in http.unmatched] == ["https://example.invalid/whatever"]
    assert isinstance(http.unmatched[0], httpx2.Request)
