from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

LLMProvider = Literal["fake", "groq", "ollama", "gemini", "openai"]
AgentBackendName = Literal["custom", "pydantic_ai", "langgraph"]
EmbeddingProvider = Literal["hash", "openai", "voyage"]
RetrievalMode = Literal["dense", "hybrid"]


class MCPServerConfig(BaseModel):
    """One MCP tool server.

    transport "stdio" spawns a subprocess; "http" connects to a streamable-HTTP
    endpoint. The command "{python}" resolves to the current interpreter, so the
    bundled servers work from any venv without configuration.
    """

    name: str
    transport: Literal["stdio", "http"] = "stdio"
    command: str | None = None  # stdio only
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: str | None = None  # http only
    enabled: bool = True


def _default_mcp_servers() -> list[MCPServerConfig]:
    # Zero-credential defaults: code search over this repository plus a *mocked*
    # GitHub server (same tool names as the official one). Swap the mock for
    # ghcr.io/github/github-mcp-server once a PAT exists — see .env.example.
    return [
        MCPServerConfig(
            name="code", command="{python}", args=["-m", "assistant.mcp_servers.code_search"]
        ),
        MCPServerConfig(
            name="github", command="{python}", args=["-m", "assistant.mcp_servers.fake_github"]
        ),
    ]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="ASSISTANT_", extra="ignore")

    debug: bool = True

    # Auth: unset (default) = open, for zero-config dev. When set, /api/*
    # requires `Authorization: Bearer <token>` and /chat requires `?token=`.
    # Production path: replace with OIDC at the gateway.
    auth_token: SecretStr | None = None

    # LLM — the provider is a config value, not a code decision.
    # "fake" is an offline deterministic provider: zero cost, no API key, used
    # as the dev/test default so the app runs out of the box.
    llm_provider: LLMProvider = "fake"
    llm_model: str = "llama-3.3-70b-versatile"
    llm_api_key: SecretStr | None = None
    llm_base_url: str | None = None  # override the provider default URL if needed

    # Agent backend — switchable so custom/pydantic_ai/langgraph can be compared
    agent_backend: AgentBackendName = "custom"

    # Steers tool choice and honesty. It deliberately does NOT enumerate topics:
    # the knowledge base is whatever documents were uploaded, so naming a fixed
    # set of subjects would make the model refuse things it can actually answer.
    system_prompt: str = (
        "You are the AI Workspace Assistant, an internal assistant for engineers. "
        "Tools: search_docs searches the team's own knowledge base — whatever "
        "documents have been added to this assistant — so call it first for "
        "questions about our systems, services, processes or documentation, and "
        "cite the source files it returns. fetch_url fetches public web pages and "
        "GitHub repositories/accounts — use it whenever the user asks about a "
        "URL or an external project. You have no other web access: never state "
        "the content of a page you did not fetch. If a tool returns nothing "
        "relevant, do not repeat a similar call and do not guess — say plainly "
        "that you could not find the answer. Answer concisely."
    )

    # Embeddings / RAG
    # "hash" is an offline feature-hashing embedder: zero cost, deterministic,
    # good enough for lexical matches — the dev/test default. Voyage arrives in
    # Phase 7 for the measured comparison.
    embedding_provider: EmbeddingProvider = "hash"
    embedding_model: str = "text-embedding-3-small"
    embedding_api_key: SecretStr | None = None
    voyage_api_key: SecretStr | None = None  # for the voyage embedding comparison
    qdrant_collection: str = "docs"
    # Optional folder to (re)ingest from. Unset by default: the knowledge
    # base starts empty and is filled at runtime via POST /api/documents.
    # Set it to keep a folder of Markdown synced (nightly job + Re-index).
    corpus_dir: Path | None = None

    # Retrieval: hybrid = dense + sparse lexical vectors fused with RRF;
    # a deterministic lexical reranker reorders the top candidates.
    retrieval_mode: RetrievalMode = "hybrid"
    rerank_enabled: bool = True

    # Conversation summarization: when the un-summarized history exceeds the
    # budget, older turns are folded into a rolling summary; the most recent
    # messages stay verbatim.
    history_char_budget: int = 8000  # ~2k tokens
    history_keep_recent: int = 6  # messages kept verbatim

    # MCP tool servers (see MCPServerConfig above; JSON via ASSISTANT_MCP_SERVERS)
    mcp_enabled: bool = True
    mcp_servers: list[MCPServerConfig] = Field(default_factory=_default_mcp_servers)

    # Rate limiting — a budget guard, not access control: it stops one stuck
    # client from draining the day's LLM quota. Limits are per session, in a
    # sliding window shared through Redis. Set a limit to 0 to disable just
    # that bucket; rate_limit_enabled=false disables all of them.
    rate_limit_enabled: bool = True
    rate_limit_turns_per_minute: int = 20  # chat turns (the expensive path)
    rate_limit_uploads_per_hour: int = 50  # document uploads + re-index

    # Infra
    redis_url: str = "redis://localhost:6379/0"
    qdrant_url: str = "http://localhost:6333"
    session_ttl_seconds: int = 60 * 60 * 24

    # Logging (always on) — structured logs, pretty console by default,
    # JSON lines when log_json=true. log_prompts dumps full prompts and
    # completions (dev-only debugging; conversations end up in logs).
    log_level: str = "INFO"
    log_json: bool = False
    log_prompts: bool = False

    # Tracing — spans are emitted when ANY destination is configured:
    # otlp_endpoint (local Jaeger via `docker compose --profile observability up`),
    # Logfire token, or Langfuse keys. Otherwise fully inert.
    otlp_endpoint: str | None = None  # e.g. http://localhost:4318

    # Observability cloud backends — inert until tokens are configured
    logfire_token: SecretStr | None = None
    langfuse_public_key: str | None = None
    langfuse_secret_key: SecretStr | None = None
    langfuse_host: str = "https://cloud.langfuse.com"
