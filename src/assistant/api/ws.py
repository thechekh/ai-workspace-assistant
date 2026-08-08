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
)
from assistant.api.schemas import SessionStarted, UserMessage
from assistant.api.turn_recorder import TurnRecorder
from assistant.llm.errors import describe_llm_error
from assistant.memory.conversation import ConversationMemory
from assistant.memory.session import SessionStore
from assistant.telemetry import (
    COST_USD_TOTAL,
    ERRORS_TOTAL,
    TURN_SECONDS,
    TURNS_TOTAL,
    current_turn_stats,
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
    """One user message: run the agent, forward events, record telemetry + audit.

    The accounting lives in TurnRecorder; this function is the conductor —
    it owns the socket, the span, the error mapping and persistence.
    """
    recorder = TurnRecorder(turn_id=uuid.uuid4().hex[:12], backend=backend, llm_model=llm_model)
    stats_token = current_turn_stats.set(recorder.stats)
    with structlog.contextvars.bound_contextvars(
        session_id=session_id, turn_id=recorder.turn_id, backend=backend
    ):
        logger.info("turn.start", user_chars=len(user_message))
        try:
            with tracer.start_as_current_span("agent.turn") as span:
                span.set_attribute("session.id", session_id)
                span.set_attribute("turn.id", recorder.turn_id)
                span.set_attribute("agent.backend", backend)

                # Bounded view: rolling summary + recent turns (full transcript in Redis)
                history = await memory.context_for(session_id)
                await store.append(session_id, ChatMessage(role="user", content=user_message))

                async for event in agent.run(history=history, user_message=user_message):
                    await websocket.send_text(event.model_dump_json())
                    recorder.observe(event)
                    if isinstance(event, FinalEvent):
                        await store.append(
                            session_id, ChatMessage(role="assistant", content=event.content)
                        )

                if recorder.error_count:
                    ERRORS_TOTAL.labels(kind="agent_event").inc(recorder.error_count)
                span.set_attribute("turn.tool_calls", len(recorder.tool_calls))
                span.set_attribute("turn.answer_chars", recorder.answer_chars)
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

        summary = recorder.summary()
        TURNS_TOTAL.labels(backend=backend).inc()
        TURN_SECONDS.labels(backend=backend).observe(summary.duration_ms / 1000)
        if summary.cost_usd:
            COST_USD_TOTAL.labels(model=llm_model).inc(summary.cost_usd)

        try:
            await websocket.send_text(summary.model_dump_json())
        except Exception:  # client may close right after `final` — stats still get logged
            logger.info("turn.summary_send_failed")

        logger.info(
            "turn.summary",
            **summary.model_dump(exclude={"type", "turn_id", "backend"}),
            llm_ms=round(recorder.stats.llm_ms),
            answer_chars=recorder.answer_chars,
        )
        try:
            await store.append_turn(session_id, recorder.record(summary))
        except Exception:
            logger.warning("turn.audit_store_failed", exc_info=True)
