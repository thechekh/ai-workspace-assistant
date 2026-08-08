"""The typed WebSocket protocol.

Client -> server: user_message
Server -> client: session, token, tool_call, tool_result, final, error

The agent event models double as wire frames, so the protocol and the agent
contract cannot drift apart.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from assistant.agent.base import (
    ErrorEvent,
    FinalEvent,
    TokenEvent,
    ToolCallEvent,
    ToolResultEvent,
)


class UserMessage(BaseModel):
    type: Literal["user_message"] = "user_message"
    # Bounded so one pasted document cannot consume a whole token budget in a
    # single turn (~2k tokens of prompt); the WS layer reports the rejection.
    content: str = Field(min_length=1, max_length=8000)


class SessionStarted(BaseModel):
    type: Literal["session"] = "session"
    session_id: str


class TurnSummary(BaseModel):
    """Per-turn stats, sent after `final` — rendered as a meta line in the UI."""

    type: Literal["turn"] = "turn"
    turn_id: str
    backend: str
    duration_ms: int
    first_token_ms: int | None = None
    llm_steps: int
    tool_calls: list[str] = Field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    usage_estimated: bool = True
    # Indicative spend at listed pay-per-token prices (0.0 for fake/unknown models)
    cost_usd: float = 0.0


class IndexedDocument(BaseModel):
    """One document currently in the knowledge base."""

    source: str
    chunks: int


class DocumentList(BaseModel):
    documents: list[IndexedDocument]
    total_chunks: int


class DocumentUploadResult(BaseModel):
    """What POST /api/documents indexed. Re-uploading a source replaces it."""

    indexed: list[IndexedDocument]
    chunks: int
    skipped: list[str] = Field(default_factory=list)


class TurnAuditEvent(BaseModel):
    """One row of a turn's timeline, with its offset from the turn's start.

    Fields are per-kind: tool_call carries tool+arguments, tool_result carries
    tool+result_chars, final carries chars, error carries message.
    """

    ms: int
    type: Literal["tool_call", "tool_result", "final", "error"]
    tool: str | None = None
    arguments: str | None = None
    result_chars: int | None = None
    chars: int | None = None
    message: str | None = None


class TurnRecord(BaseModel):
    """A replayable turn: the same stats the UI shows, plus the timeline.

    This is a real contract — it round-trips through Redis and is served by
    `GET /api/sessions/{id}/turns[/{turn_id}]` to the frontend — so it is
    typed rather than a bare dict.
    """

    turn_id: str
    backend: str
    duration_ms: int
    first_token_ms: int | None = None
    llm_steps: int
    prompt_tokens: int = 0
    completion_tokens: int = 0
    usage_estimated: bool = True
    cost_usd: float = 0.0
    tool_calls: list[str] = Field(default_factory=list)
    events: list[TurnAuditEvent] = Field(default_factory=list)


class SessionTurns(BaseModel):
    session_id: str
    count: int
    turns: list[TurnRecord]


ServerEvent = Annotated[
    SessionStarted
    | TokenEvent
    | ToolCallEvent
    | ToolResultEvent
    | FinalEvent
    | ErrorEvent
    | TurnSummary,
    Field(discriminator="type"),
]
