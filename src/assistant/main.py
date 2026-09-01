from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import redis.asyncio as aioredis
from fastapi import FastAPI, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from qdrant_client import AsyncQdrantClient
from redis.asyncio import Redis

from assistant.agent.base import AgentBackend
from assistant.agent.registry import build_agents
from assistant.agent.tools import Tool, ToolRegistry, make_fetch_url, make_search_docs
from assistant.agent.tools.fetch import new_http_client
from assistant.api.rate_limit import RateLimiter
from assistant.api.routes import router as api_router
from assistant.api.ws import router as ws_router
from assistant.config import Settings
from assistant.llm.client import LLMClient, OpenAICompatibleLLM, build_llm
from assistant.logs import configure_logging
from assistant.mcp.registry import MCPRegistry
from assistant.memory.conversation import ConversationMemory
from assistant.memory.session import SessionStore
from assistant.memory.summarizer import build_summarizer
from assistant.observability import configure_observability
from assistant.rag.embeddings import build_embedder
from assistant.rag.rerank import LexicalReranker
from assistant.rag.retriever import Retriever
from assistant.rag.store import VectorStore
from assistant.telemetry import InstrumentedLLM


def _redis_from_url(url: str) -> Redis:
    if url.startswith("fakeredis://"):
        # Zero-infrastructure dev mode: in-memory store, sessions don't survive restarts.
        from fakeredis import FakeAsyncRedis  # dev dependency, imported lazily

        return FakeAsyncRedis(decode_responses=True)
    return aioredis.from_url(url, decode_responses=True)


@dataclass
class Runtime:
    """Everything a request needs, built once at startup and closed at shutdown.

    Assembling this separately from `create_app` keeps the wiring readable and
    means tests can replace whole collaborators without the factory growing
    another `if x is None` branch.
    """

    settings: Settings
    redis: Redis
    llm: LLMClient
    session_store: SessionStore
    memory: ConversationMemory
    rate_limiter: RateLimiter
    agents: dict[str, AgentBackend]
    http_client: httpx.AsyncClient
    qdrant: AsyncQdrantClient | None = None
    # The document store backing /api/documents; None when a retriever was
    # injected (tests) and there is no live Qdrant to write to.
    vector_store: VectorStore | None = None
    mcp_registry: MCPRegistry | None = None
    mcp_tool_names: list[str] = field(default_factory=list)
    owns_redis: bool = True

    async def aclose(self) -> None:
        """Release every resource this runtime owns, best-effort and in order."""
        if self.mcp_registry is not None:
            await self.mcp_registry.close()
        await self.http_client.aclose()
        # The provider SDK keeps its own pool; only the hosted client has one.
        inner = getattr(self.llm, "_inner", self.llm)
        if isinstance(inner, OpenAICompatibleLLM):
            await inner.aclose()
        if self.owns_redis:
            await self.redis.aclose()
        if self.qdrant is not None:
            await self.qdrant.close()


async def build_runtime(
    settings: Settings,
    *,
    redis_client: Redis | None = None,
    llm: LLMClient | None = None,
    agent: AgentBackend | None = None,
    retriever: Retriever | None = None,
) -> Runtime:
    """Wire the whole application graph. Overrides are for tests."""
    redis = redis_client or _redis_from_url(settings.redis_url)
    # One telemetry seam for every provider (FakeLLM included, so tests run
    # the same path): span + metrics + token usage per LLM step.
    resolved_llm: LLMClient = InstrumentedLLM(
        llm or build_llm(settings),
        provider=settings.llm_provider,
        model=settings.llm_model,
        log_prompts=settings.log_prompts,
    )

    qdrant: AsyncQdrantClient | None = None
    resolved_retriever = retriever
    if agent is None and resolved_retriever is None:
        qdrant = AsyncQdrantClient(url=settings.qdrant_url)
        resolved_retriever = Retriever(
            build_embedder(settings),
            VectorStore(qdrant, settings.qdrant_collection),
            mode=settings.retrieval_mode,
            reranker=LexicalReranker() if settings.rerank_enabled else None,
        )
    # /api/documents writes to whatever store the retriever reads from — so an
    # injected retriever (tests, in-memory Qdrant) gets a working API too.
    vector_store = resolved_retriever.store if resolved_retriever else None

    mcp_registry: MCPRegistry | None = None
    mcp_tools: list[Tool] = []
    if agent is None and settings.mcp_enabled and settings.mcp_servers:
        mcp_registry = MCPRegistry(settings.mcp_servers)
        mcp_tools = await mcp_registry.start()

    # One pooled outbound client for the whole app, closed on shutdown.
    http_client = new_http_client()
    native_tools = [make_search_docs(resolved_retriever)] if resolved_retriever else []
    native_tools.append(make_fetch_url(client=http_client))
    tools = ToolRegistry(native_tools + mcp_tools)

    agents = (
        {settings.agent_backend: agent}
        if agent is not None
        else build_agents(settings, resolved_llm, tools=tools)
    )
    if settings.agent_backend not in agents:
        raise NotImplementedError(f"unknown agent backend {settings.agent_backend!r}")

    session_store = SessionStore(redis, ttl_seconds=settings.session_ttl_seconds)
    return Runtime(
        settings=settings,
        redis=redis,
        llm=resolved_llm,
        session_store=session_store,
        rate_limiter=RateLimiter(redis, enabled=settings.rate_limit_enabled),
        memory=ConversationMemory(
            session_store,
            build_summarizer(settings, resolved_llm),
            char_budget=settings.history_char_budget,
            keep_recent=settings.history_keep_recent,
        ),
        agents=agents,
        http_client=http_client,
        qdrant=qdrant,
        vector_store=vector_store,
        mcp_registry=mcp_registry,
        mcp_tool_names=[tool.name for tool in mcp_tools],
        owns_redis=redis_client is None,
    )


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
    configure_logging(app_settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        runtime = await build_runtime(
            app_settings,
            redis_client=redis_client,
            llm=llm,
            agent=agent,
            retriever=retriever,
        )
        app.state.settings = runtime.settings
        app.state.session_store = runtime.session_store
        app.state.memory = runtime.memory
        app.state.rate_limiter = runtime.rate_limiter
        app.state.agents = runtime.agents
        app.state.default_backend = runtime.settings.agent_backend
        # Live dependency handles for the deep health check (/api/health).
        app.state.redis = runtime.redis
        app.state.qdrant = runtime.qdrant
        app.state.mcp_registry = runtime.mcp_registry
        app.state.mcp_tool_names = runtime.mcp_tool_names
        app.state.vector_store = runtime.vector_store
        try:
            yield
        finally:
            await runtime.aclose()

    app = FastAPI(title="AI Workspace Assistant", lifespan=lifespan)
    app.include_router(ws_router)
    app.include_router(api_router)
    configure_observability(app, app_settings)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        # Prometheus scrape target (counters/histograms from assistant.telemetry).
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

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


def __getattr__(name: str) -> object:
    """Build the ASGI app lazily, on first attribute access.

    uvicorn resolves `assistant.main:app` with getattr, so the documented run
    command is unchanged — but merely *importing* this module (as the test
    suite does) no longer reads `.env`, reconfigures global logging, or
    installs an OTLP tracer provider pointed at a developer's local Jaeger.
    """
    if name == "app":
        application = create_app()
        globals()["app"] = application  # cache: later access skips this hook
        return application
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
