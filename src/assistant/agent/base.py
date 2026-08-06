"""The contract every agent backend implements.

All three runtimes (custom loop, Pydantic AI, LangGraph) receive the same
inputs and emit the same event stream, so the WebSocket layer and the frontend
never care which backend is active.
"""

from collections.abc import AsyncIterator
from typing import Literal, Protocol

from pydantic import BaseModel, Field


class ToolCall(BaseModel):
    """A tool invocation requested by the assistant (mirrors the OpenAI wire shape)."""

    id: str
    name: str
    arguments: str  # raw JSON string, exactly as produced by the model


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    tool_calls: list[ToolCall] | None = None  # assistant turns only
    tool_call_id: str | None = None  # tool turns only


class TokenEvent(BaseModel):
    type: Literal["token"] = "token"
    content: str


class ToolCallEvent(BaseModel):
    type: Literal["tool_call"] = "tool_call"
    tool: str
    arguments: dict[str, object] = Field(default_factory=dict)


class ToolResultEvent(BaseModel):
    type: Literal["tool_result"] = "tool_result"
    tool: str
    result: str


class FinalEvent(BaseModel):
    type: Literal["final"] = "final"
    content: str


class ErrorEvent(BaseModel):
    type: Literal["error"] = "error"
    message: str


AgentEvent = TokenEvent | ToolCallEvent | ToolResultEvent | FinalEvent | ErrorEvent


class AgentBackend(Protocol):
    def run(self, history: list[ChatMessage], user_message: str) -> AsyncIterator[AgentEvent]:
        """Stream agent events for one user turn.

        `history` is the prior conversation (without the current message);
        the backend is responsible for composing the full prompt. The stream
        must end with a FinalEvent (or ErrorEvent).
        """
        ...
