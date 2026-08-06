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
    content: str = Field(min_length=1)


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
