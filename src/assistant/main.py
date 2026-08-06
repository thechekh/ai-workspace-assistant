from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import redis.asyncio as aioredis
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from qdrant_client import AsyncQdrantClient
from redis.asyncio import Redis

from assistant.agent.base import AgentBackend
from assistant.agent.registry import build_agents
from assistant.agent.tools import Tool, ToolRegistry, make_search_docs
from assistant.api.routes import router as api_router
from assistant.api.ws import router as ws_router
from assistant.config import Settings
from assistant.llm.client import LLMClient, build_llm
from assistant.mcp.registry import MCPRegistry
from assistant.memory.conversation import ConversationMemory
from assistant.memory.session import SessionStore
from assistant.memory.summarizer import build_summarizer
from assistant.observability import configure_observability
from assistant.rag.embeddings import build_embedder
from assistant.rag.rerank import LexicalReranker
from assistant.rag.retriever import Retriever
from assistant.rag.store import VectorStore


def _redis_from_url(url: str) -> Redis:
    if url.startswith("fakeredis://"):
        # Zero-infrastructure dev mode: in-memory store, sessions don't survive restarts.
        from fakeredis import FakeAsyncRedis  # dev dependency, imported lazily

        return FakeAsyncRedis(decode_responses=True)
    return aioredis.from_url(url, decode_responses=True)


def create_app(
    settings: Settings | None = None,
    *,
    redis_client: Redis | None = None,
    llm: LLMClient | None = None,
    agent: AgentBackend | None = None,
    retriever: Retriever | None = None,
) -> FastAPI:
    """App factory. The keyword overrides exist for tests (fakeredis, FakeLLM, :memory: Qdrant)."""
    app_settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        owns_redis = redis_client is None
        client = redis_client or _redis_from_url(app_settings.redis_url)
        resolved_llm = llm or build_llm(app_settings)

        qdrant: AsyncQdrantClient | None = None
        resolved_retriever = retriever
        if agent is None and resolved_retriever is None:
            qdrant = AsyncQdrantClient(url=app_settings.qdrant_url)
            resolved_retriever = Retriever(
                build_embedder(app_settings),
                VectorStore(qdrant, app_settings.qdrant_collection),
                mode=app_settings.retrieval_mode,
                reranker=LexicalReranker() if app_settings.rerank_enabled else None,
            )

        mcp_registry: MCPRegistry | None = None
        mcp_tools: list[Tool] = []
        if agent is None and app_settings.mcp_enabled and app_settings.mcp_servers:
            mcp_registry = MCPRegistry(app_settings.mcp_servers)
            mcp_tools = await mcp_registry.start()

        native_tools = [make_search_docs(resolved_retriever)] if resolved_retriever else []
        all_tools = native_tools + mcp_tools
        tools = ToolRegistry(all_tools) if all_tools else None

        agents = (
            {app_settings.agent_backend: agent}
            if agent is not None
            else build_agents(app_settings, resolved_llm, tools=tools)
        )
        if app_settings.agent_backend not in agents:
            raise NotImplementedError(
                f"agent backend {app_settings.agent_backend!r} arrives in a later phase"
            )

        session_store = SessionStore(client, ttl_seconds=app_settings.session_ttl_seconds)
        app.state.settings = app_settings
        app.state.session_store = session_store
        app.state.memory = ConversationMemory(
            session_store,
            build_summarizer(app_settings, resolved_llm),
            char_budget=app_settings.history_char_budget,
            keep_recent=app_settings.history_keep_recent,
        )
        app.state.agents = agents
        app.state.default_backend = app_settings.agent_backend
        try:
            yield
        finally:
            if mcp_registry is not None:
                await mcp_registry.close()
            if owns_redis:
                await client.aclose()
            if qdrant is not None:
                await qdrant.close()

    app = FastAPI(title="AI Workspace Assistant", lifespan=lifespan)
    app.include_router(ws_router)
    app.include_router(api_router)
    configure_observability(app, app_settings)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    if app_settings.debug:
        dev_page = Path(__file__).parent / "static" / "dev.html"

        @app.get("/dev", include_in_schema=False)
        async def dev_console() -> FileResponse:
            return FileResponse(dev_page)

    # Serve the built Vue SPA at / when it exists (mounted last, so API routes win).
    frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if frontend_dist.is_dir():
        app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")

    return app


app = create_app()
