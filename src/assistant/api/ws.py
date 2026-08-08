import time
import uuid

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from assistant.agent.base import (
    AgentBackend,
    ChatMessage,
    ErrorEvent,
    FinalEvent,
    TokenEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from assistant.api.schemas import SessionStarted, TurnSummary, UserMessage
from assistant.llm.errors import describe_llm_error
from assistant.memory.conversation import ConversationMemory
from assistant.memory.session import SessionStore
from assistant.telemetry import (
    COST_USD_TOTAL,
    ERRORS_TOTAL,
    TURN_SECONDS,
    TURNS_TOTAL,
    TurnStats,
    current_turn_stats,
    estimate_cost_usd,
    tracer,
)

router = APIRouter()
logger = structlog.get_logger("assistant.ws")


def _elapsed_ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


@router.websocket("/chat")
async def chat_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()

    # Optional bearer auth (browsers can't set WS headers -> ?token= query param)
    expected = websocket.app.state.settings.auth_token
    if expected is not None and websocket.query_params.get("token") != expected.get_secret_value():
        await websocket.close(code=1008, reason="missing or invalid token")
        return

    store: SessionStore = websocket.app.state.session_store
    memory: ConversationMemory = websocket.app.state.memory
    agents: dict[str, AgentBackend] = websocket.app.state.agents
    default_backend: str = websocket.app.state.default_backend
    settings = websocket.app.state.settings
    # Cost is priced per model; the fake provider is free by definition.
    llm_model: str = "" if settings.llm_provider == "fake" else settings.llm_model

    # ?backend= picks the runtime for this connection (side-by-side comparison);
    # unknown/absent values fall back to the configured default.
    requested_backend = websocket.query_params.get("backend") or default_backend
    if requested_backend not in agents:
        requested_backend = default_backend
    agent = agents[requested_backend]

    # Reconnecting clients pass ?session_id=... to resume their history.
    session_id = websocket.query_params.get("session_id") or SessionStore.new_session_id()
    await websocket.send_text(SessionStarted(session_id=session_id).model_dump_json())
    logger.info("ws.connected", session_id=session_id, backend=requested_backend)

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                incoming = UserMessage.model_validate_json(raw)
            except ValidationError:
                ERRORS_TOTAL.labels(kind="invalid_message").inc()
                await websocket.send_text(
                    ErrorEvent(
                        message="invalid message, expected {type: user_message, content}"
                    ).model_dump_json()
                )
                continue
            await _handle_turn(
                websocket,
                store,
                memory,
                agent,
                requested_backend,
                session_id,
                incoming.content,
                llm_model,
            )
    except WebSocketDisconnect:
        logger.info("ws.disconnected", session_id=session_id)
        return


async def _handle_turn(
    websocket: WebSocket,
    store: SessionStore,
    memory: ConversationMemory,
    agent: AgentBackend,
    backend: str,
    session_id: str,
    user_message: str,
    llm_model: str,
) -> None:
    """One user message: run the agent, forward events, record telemetry + audit."""
    turn_id = uuid.uuid4().hex[:12]
    started = time.perf_counter()

    audit: list[dict[str, object]] = []
    tool_names: list[str] = []
    first_token_ms: int | None = None
    answer_chars = 0

    stats = TurnStats()
    stats_token = current_turn_stats.set(stats)
    with structlog.contextvars.bound_contextvars(
        session_id=session_id, turn_id=turn_id, backend=backend
    ):
        logger.info("turn.start", user_chars=len(user_message))
        try:
            with tracer.start_as_current_span("agent.turn") as span:
                span.set_attribute("session.id", session_id)
                span.set_attribute("turn.id", turn_id)
                span.set_attribute("agent.backend", backend)

                # Bounded view: rolling summary + recent turns (full transcript in Redis)
                history = await memory.context_for(session_id)
                await store.append(session_id, ChatMessage(role="user", content=user_message))

                async for event in agent.run(history=history, user_message=user_message):
                    await websocket.send_text(event.model_dump_json())
                    if isinstance(event, TokenEvent):
                        if first_token_ms is None:
                            first_token_ms = _elapsed_ms(started)
                        answer_chars += len(event.content)
                    elif isinstance(event, ToolCallEvent):
                        tool_names.append(event.tool)
                        audit.append(
                            {
                                "ms": _elapsed_ms(started),
                                "type": "tool_call",
                                "tool": event.tool,
                                "arguments": str(event.arguments)[:300],
                            }
                        )
                    elif isinstance(event, ToolResultEvent):
                        audit.append(
                            {
                                "ms": _elapsed_ms(started),
                                "type": "tool_result",
                                "tool": event.tool,
                                "result_chars": len(event.result),
                            }
                        )
                    elif isinstance(event, FinalEvent):
                        await store.append(
                            session_id, ChatMessage(role="assistant", content=event.content)
                        )
                        audit.append(
                            {
                                "ms": _elapsed_ms(started),
                                "type": "final",
                                "chars": len(event.content),
                            }
                        )
                    elif isinstance(event, ErrorEvent):
                        ERRORS_TOTAL.labels(kind="agent_event").inc()
                        audit.append(
                            {
                                "ms": _elapsed_ms(started),
                                "type": "error",
                                "message": event.message[:300],
                            }
                        )
                span.set_attribute("turn.tool_calls", len(tool_names))
                span.set_attribute("turn.answer_chars", answer_chars)
        except WebSocketDisconnect:
            # The client went away mid-turn (closed tab, navigation). That is
            # routine, not a server error — let chat_endpoint end the loop
            # without polluting error metrics or logging a traceback.
            logger.info("turn.abandoned")
            raise
        except Exception as exc:
            kind, message = describe_llm_error(exc) or (
                "turn_exception",
                "server error — check server logs (is Redis/Qdrant running?)",
            )
            ERRORS_TOTAL.labels(kind=kind).inc()
            logger.exception("turn.failed", kind=kind)
            await websocket.send_text(ErrorEvent(message=message).model_dump_json())
            return
        finally:
            current_turn_stats.reset(stats_token)

        duration_ms = _elapsed_ms(started)
        TURNS_TOTAL.labels(backend=backend).inc()
        TURN_SECONDS.labels(backend=backend).observe(duration_ms / 1000)

        # The pydantic-ai backend runs its own model layer (not InstrumentedLLM),
        # so fall back to structural estimates when the wrapper saw nothing.
        llm_steps = stats.llm_steps or len(tool_names) + 1
        usage_estimated = stats.usage_estimated or stats.llm_steps == 0
        completion_tokens = stats.completion_tokens or answer_chars // 4
        cost_usd = round(estimate_cost_usd(llm_model, stats.prompt_tokens, completion_tokens), 6)
        if cost_usd:
            COST_USD_TOTAL.labels(model=llm_model).inc(cost_usd)

        summary = TurnSummary(
            turn_id=turn_id,
            backend=backend,
            duration_ms=duration_ms,
            first_token_ms=first_token_ms,
            llm_steps=llm_steps,
            tool_calls=tool_names,
            prompt_tokens=stats.prompt_tokens,
            completion_tokens=completion_tokens,
            usage_estimated=usage_estimated,
            cost_usd=cost_usd,
        )
        try:
            await websocket.send_text(summary.model_dump_json())
        except Exception:  # client may close right after `final` — stats still get logged
            logger.info("turn.summary_send_failed")
        logger.info(
            "turn.summary",
            duration_ms=duration_ms,
            first_token_ms=first_token_ms,
            llm_steps=llm_steps,
            llm_ms=round(stats.llm_ms),
            tool_calls=tool_names,
            prompt_tokens=stats.prompt_tokens,
            completion_tokens=completion_tokens,
            usage_estimated=usage_estimated,
            cost_usd=cost_usd,
            answer_chars=answer_chars,
        )
        try:
            await store.append_turn(
                session_id,
                {
                    "turn_id": turn_id,
                    "backend": backend,
                    "duration_ms": duration_ms,
                    "first_token_ms": first_token_ms,
                    "llm_steps": llm_steps,
                    "prompt_tokens": stats.prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "usage_estimated": usage_estimated,
                    "cost_usd": cost_usd,
                    "tool_calls": tool_names,
                    "events": audit,
                },
            )
        except Exception:
            logger.warning("turn.audit_store_failed", exc_info=True)
