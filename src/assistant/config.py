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

    system_prompt: str = (
        "You are the AI Workspace Assistant, an internal assistant for engineers. "
        "You have a search_docs tool over the internal engineering documentation "
        "(architecture, service catalog, deployment, guidelines, onboarding). "
        "When a question concerns our systems, services, or processes, call "
        "search_docs first and ground your answer in the results, citing the "
        "source files. Answer concisely; if the docs do not cover it, say so."
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

    # Infra
    redis_url: str = "redis://localhost:6379/0"
    qdrant_url: str = "http://localhost:6333"
    session_ttl_seconds: int = 60 * 60 * 24

    # Observability — fully inert until tokens are configured
    logfire_token: SecretStr | None = None
    langfuse_public_key: str | None = None
    langfuse_secret_key: SecretStr | None = None
    langfuse_host: str = "https://cloud.langfuse.com"
