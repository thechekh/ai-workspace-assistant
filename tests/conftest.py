import asyncio

import pytest
from fakeredis import FakeAsyncRedis
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic_settings import SettingsConfigDict
from qdrant_client import AsyncQdrantClient

from assistant.config import Settings
from assistant.llm.client import FakeLLM
from assistant.main import create_app
from assistant.rag.chunking import chunk_markdown
from assistant.rag.embeddings import HashEmbedder
from assistant.rag.rerank import LexicalReranker
from assistant.rag.retriever import Retriever
from assistant.rag.sparse import encode_sparse
from assistant.rag.store import VectorStore

CORPUS_MD = """# Services

## billing-service

Generates PDF invoices on a nightly schedule and posts them to customers.

## auth-service

Handles authentication with OIDC and issues JWT access tokens.
"""


class HermeticSettings(Settings):
    """Settings that never read a developer's local .env — tests stay deterministic."""

    model_config = SettingsConfigDict(env_file=None)


async def build_seeded_retriever_async(
    corpus_md: str = CORPUS_MD, source: str = "sample.md"
) -> Retriever:
    """In-memory Qdrant + hash embedder, pre-seeded — no containers, no network."""
    client = AsyncQdrantClient(":memory:")
    embedder = HashEmbedder()
    store = VectorStore(client, "test_docs")
    chunks = chunk_markdown(corpus_md, source=source)
    dense = await embedder.embed([chunk.text for chunk in chunks])
    sparse = [encode_sparse(chunk.text) for chunk in chunks]
    await store.ensure_collection(embedder.dimension)
    await store.upsert(chunks, dense, sparse)
    # Mirror the app default: hybrid search + lexical rerank
    return Retriever(embedder, store, mode="hybrid", reranker=LexicalReranker())


def build_seeded_retriever(corpus_md: str = CORPUS_MD, source: str = "sample.md") -> Retriever:
    """Sync wrapper for non-async contexts (fixtures); async tests use the _async variant."""
    return asyncio.run(build_seeded_retriever_async(corpus_md, source))


@pytest.fixture(params=["custom", "pydantic_ai", "langgraph"])
def settings(request: pytest.FixtureRequest) -> Settings:
    # Parametrized over agent backends: the whole WS suite must pass identically
    # on every runtime. mcp_enabled=False keeps these tests fast — MCP stdio
    # servers get their own integration tests in test_mcp.py.
    return HermeticSettings(llm_provider="fake", agent_backend=request.param, mcp_enabled=False)


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(
        settings,
        redis_client=FakeAsyncRedis(decode_responses=True),
        llm=FakeLLM(),
        retriever=build_seeded_retriever(),
    )


@pytest.fixture
def client(app: FastAPI):
    # Context manager runs the lifespan (wires session store + agent + tools).
    with TestClient(app) as test_client:
        yield test_client
