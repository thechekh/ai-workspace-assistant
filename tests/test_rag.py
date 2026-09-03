"""RAG pipeline tests — hash embedder + in-memory Qdrant: no containers, no cost."""

import httpx
import pytest
import respx
from qdrant_client import AsyncQdrantClient

from assistant.rag.chunking import chunk_markdown
from assistant.rag.embeddings import HashEmbedder, VoyageEmbedder, build_embedder
from assistant.rag.ingest import ingest
from assistant.rag.rerank import LexicalReranker, query_overlap
from assistant.rag.retriever import Retriever
from assistant.rag.sparse import encode_sparse
from assistant.rag.store import RetrievedChunk, VectorStore
from tests.conftest import HermeticSettings

SAMPLE_MD = """# Services

## billing-service

Generates PDF invoices on a nightly schedule and posts them to customers.

## auth-service

Handles authentication with OIDC and issues JWT access tokens.
"""


def test_chunk_markdown_builds_heading_breadcrumbs():
    chunks = chunk_markdown(SAMPLE_MD, source="services.md")
    headings = {chunk.heading for chunk in chunks}
    assert "Services > billing-service" in headings
    assert "Services > auth-service" in headings
    assert all(chunk.source == "services.md" for chunk in chunks)
    # breadcrumb is prepended to the embedded text
    billing = next(c for c in chunks if c.heading == "Services > billing-service")
    assert billing.text.startswith("Services > billing-service")


def test_chunk_ids_are_deterministic():
    first = chunk_markdown(SAMPLE_MD, source="services.md")
    second = chunk_markdown(SAMPLE_MD, source="services.md")
    assert [c.id for c in first] == [c.id for c in second]
    # different source -> different ids
    other = chunk_markdown(SAMPLE_MD, source="other.md")
    assert [c.id for c in first] != [c.id for c in other]


def test_chunk_markdown_splits_long_sections():
    long_md = "# Doc\n\n" + "\n\n".join(f"Paragraph {i}. " + "word " * 150 for i in range(10))
    chunks = chunk_markdown(long_md, source="long.md")
    assert len(chunks) > 1
    assert all(len(chunk.text) < 3000 for chunk in chunks)


def test_chunk_markdown_keeps_code_fences_intact():
    md = "# Doc\n\nIntro paragraph.\n\n```python\nline1 = 1\n\nline2 = 2\n```\n\nOutro."
    chunks = chunk_markdown(md, source="code.md")
    joined = "\n".join(chunk.text for chunk in chunks)
    assert "line1 = 1\n\nline2 = 2" in joined  # blank line inside fence not split


async def test_hash_embedder_deterministic_and_normalized():
    embedder = HashEmbedder()
    [first] = await embedder.embed(["hello world"])
    [second] = await embedder.embed(["hello world"])
    assert first == second
    assert len(first) == embedder.dimension
    assert abs(sum(value * value for value in first) - 1.0) < 1e-6


async def test_ingest_and_retrieve_roundtrip(tmp_path):
    (tmp_path / "svc.md").write_text(SAMPLE_MD, encoding="utf-8")
    client = AsyncQdrantClient(":memory:")
    settings = HermeticSettings(embedding_provider="hash", qdrant_collection="test_docs")

    count = await ingest(tmp_path, settings, client=client)
    assert count >= 2

    retriever = Retriever(HashEmbedder(), VectorStore(client, "test_docs"))
    results = await retriever.search("Which service generates PDF invoices?", limit=2)
    assert results
    assert "billing-service" in results[0].text

    # payload filter restricts results to one source document
    filtered = await retriever.search("authentication tokens", limit=5, source="svc.md")
    assert filtered
    assert all(result.source == "svc.md" for result in filtered)

    # re-ingesting is idempotent: deterministic ids overwrite, count stays stable
    await ingest(tmp_path, settings, client=client)
    info = await client.count("test_docs")
    assert info.count == count


def test_sparse_encoding_is_deterministic():
    first = encode_sparse("Deploy the billing service to EKS")
    second = encode_sparse("Deploy the billing service to EKS")
    assert first == second
    indices, values = first
    assert len(indices) == len(values) > 0
    assert indices == sorted(indices)
    # repeated tokens boost the value instead of duplicating the index
    single = dict(zip(*encode_sparse("deploy"), strict=True))
    repeated = dict(zip(*encode_sparse("deploy deploy deploy"), strict=True))
    assert next(iter(repeated.values())) > next(iter(single.values()))


async def test_hybrid_exact_token_match_ranks_first(tmp_path):
    (tmp_path / "svc.md").write_text(SAMPLE_MD, encoding="utf-8")
    client = AsyncQdrantClient(":memory:")
    settings = HermeticSettings(embedding_provider="hash", qdrant_collection="test_docs")
    await ingest(tmp_path, settings, client=client)

    retriever = Retriever(
        HashEmbedder(), VectorStore(client, "test_docs"), mode="hybrid", reranker=None
    )
    results = await retriever.search("OIDC access tokens", limit=2)
    assert results
    assert "auth-service" in results[0].text


def test_lexical_reranker_orders_by_overlap():
    candidates = [
        RetrievedChunk(
            text="totally unrelated content about lunch", source="a", heading="", score=0.9
        ),
        RetrievedChunk(
            text="invoice generation billing nightly job", source="b", heading="", score=0.1
        ),
    ]
    reranked = LexicalReranker().rerank("invoice generation", candidates, limit=2)
    assert reranked[0].source == "b"


async def test_voyage_embedder_calls_api_with_auth():
    with respx.mock(assert_all_called=True) as router:
        route = router.post("https://api.voyageai.com/v1/embeddings").mock(
            return_value=httpx.Response(
                200, json={"data": [{"embedding": [0.1] * 1024}, {"embedding": [0.2] * 1024}]}
            )
        )
        embedder = VoyageEmbedder(model="voyage-3", api_key="voyage-test-key")
        vectors = await embedder.embed(["one", "two"])

    assert len(vectors) == 2
    assert len(vectors[0]) == embedder.dimension == 1024
    request = route.calls.last.request
    assert request.headers["Authorization"] == "Bearer voyage-test-key"


def test_build_embedder_requires_voyage_key():
    settings = HermeticSettings(embedding_provider="voyage")
    with pytest.raises(ValueError, match="ASSISTANT_VOYAGE_API_KEY"):
        build_embedder(settings)


def test_identifiers_are_searchable_by_their_words():
    """camelCase/snake_case split at tokenization — the live 'meter percentage' miss.

    `completedPercentage` lowercased-then-tokenized is one opaque token; the
    query word "percentage" could never match it, so the relevance gate
    discarded the exact chunk that held the answer. Splitting must keep the
    whole identifier AND its subwords, on all three consumers (sparse, gate,
    reranker share `tokenize`).
    """
    from assistant.rag.sparse import tokenize

    tokens = tokenize("let completedPercentage = completedAmount / totalAmount;")
    assert "completedpercentage" in tokens  # exact identifier still searchable
    assert {"percentage", "completed", "total"} <= set(tokens)

    assert {"snake", "case"} <= set(tokenize("snake_case_name"))
    assert "server" in tokenize("HTTP2Server")

    # The gate that failed live now passes for the natural-language question.
    assert query_overlap("meter percentage formula", "completedPercentage = a / b") >= 1
