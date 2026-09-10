"""The contract every agent backend implements.

All three runtimes (custom loop, Pydantic AI, LangGraph) receive the same
inputs and emit the same event stream, so the WebSocket layer and the frontend
never care which backend is active.
"""

from collections.abc import AsyncGenerator
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

# How much of a tool result reaches the UI. Defined once: three backends
# drifting on this would silently change what users see per runtime.
EVENT_RESULT_LIMIT = 1500

# How many LLM steps one turn may take before the loop gives up, and what the
# user reads when it does. Defined once for the same reason: the custom loop,
# LangGraph's recursion limit and Pydantic AI's request limit all derive from
# this number, so a runaway turn costs the same on every runtime.
MAX_ITERATIONS = 6
ITERATION_LIMIT_MESSAGE = (
    "I hit the tool-call limit for one turn without reaching a final "
    "answer. Please rephrase or narrow the question."
)


def truncate_for_event(result: str, limit: int = EVENT_RESULT_LIMIT) -> str:
    """Clip a tool result for a ToolResultEvent, marking that it was clipped."""
    return result if len(result) <= limit else result[:limit] + "…"


class AgentBackend(Protocol):
    def run(
        self, history: list[ChatMessage], user_message: str
    ) -> AsyncGenerator[AgentEvent, None]:
        """Stream agent events for one user turn.

        `history` is the prior conversation (without the current message);
        the backend is responsible for composing the full prompt. The stream
        must end with a FinalEvent (or ErrorEvent). It is an async generator,
        so a consumer that stops early can `aclose()` it and have the
        backend's cleanup run in the consumer's own task.
        """
        ...
