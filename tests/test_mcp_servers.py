"""The two bundled MCP servers, called in-process.

`test_mcp.py` proves the stdio plumbing by spawning them; that runs the
server code in a subprocess, where coverage cannot see it. These tests call
the tool functions directly, so the logic inside each server is measured too.
"""

from assistant.mcp_servers import code_search, fake_github


def test_search_code_finds_this_repository_and_caps_results():
    hits = code_search.search_code("class CustomAgent", max_results=3)
    assert "custom.py" in hits
    assert len(hits.splitlines()) <= 3


def test_search_code_reports_invalid_regex_and_misses_honestly():
    assert code_search.search_code("(unclosed").startswith("error: invalid regex")
    # A regex whose *match* ("...token...") is written nowhere in the repo,
    # this file included — the group keeps the literal from matching itself.
    miss = code_search.search_code("qqzz_no_such_tok(en)_9f8e7d")
    assert "no matches" in miss
    assert "retry with different terms" in miss


def test_read_file_returns_numbered_lines_and_guards_the_root():
    listing = code_search.read_file("pyproject.toml", start_line=1, max_lines=2)
    assert listing.startswith("1: ")
    assert len(listing.splitlines()) == 2

    assert code_search.read_file("../outside.txt").startswith("error: path escapes")
    assert code_search.read_file("does/not/exist.py").startswith("error: no such file")
    assert "no lines at 99999" in code_search.read_file("pyproject.toml", start_line=99999)


def test_fake_github_lists_and_reads_pull_requests():
    listing = fake_github.list_pull_requests(state="open", limit=2)
    assert "#142" in listing
    assert len(listing.splitlines()) == 2
    assert "#140" in fake_github.list_pull_requests(state="merged")
    assert fake_github.list_pull_requests(state="closed").startswith("no pull requests")

    detail = fake_github.get_pull_request(142)
    assert "LangGraph backend" in detail
    assert "branch: feat/agent-langgraph" in detail
    assert fake_github.get_pull_request(1).startswith("error: pull request #1 not found")


def test_fake_github_lists_issues():
    assert "#135" in fake_github.list_issues()
    assert fake_github.list_issues(state="closed").startswith("no issues")
