import asyncio
import contextlib
import secrets
import uuid

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import TypeAdapter, ValidationError

from assistant.agent.base import (
    AgentBackend,
    ChatMessage,
    ErrorEvent,
    FinalEvent,
)
from assistant.agent.output_guard import correct_unsupported_action_claims
from assistant.api.rate_limit import RateLimiter
from assistant.api.schemas import CancelRequest, ClientMessage, SessionStarted
from assistant.api.turn_recorder import TurnRecorder
from assistant.config import Settings
from assistant.llm.errors import describe_llm_error
from assistant.memory.conversation import ConversationMemory
from assistant.memory.session import SessionStore
from assistant.telemetry import (
    CANCELLED_TOTAL,
    COST_USD_TOTAL,
    ERRORS_TOTAL,
    RATE_LIMITED_TOTAL,
    TURN_SECONDS,
    TURNS_TOTAL,
    current_turn_stats,
    tracer,
)

router = APIRouter()
logger = structlog.get_logger("assistant.ws")


@router.websocket("/chat")
async def chat_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()

    # Optional bearer auth (browsers can't set WS headers -> ?token= query param).
    # compare_digest for the same reason as the HTTP guard: `!=` returns early
    # on the first differing byte and leaks the secret to a timing attack.
    expected = websocket.app.state.settings.auth_token
    if expected is not None and not secrets.compare_digest(
        websocket.query_params.get("token", ""), expected.get_secret_value()
    ):
        await websocket.close(code=1008, reason="missing or invalid token")
        return

    store: SessionStore = websocket.app.state.session_store
    memory: ConversationMemory = websocket.app.state.memory
    limiter: RateLimiter = websocket.app.state.rate_limiter
    agents: dict[str, AgentBackend] = websocket.app.state.agents
    default_backend: str = websocket.app.state.default_backend
    settings: Settings = websocket.app.state.settings
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

    # The turn runs as a task rather than inline, so this loop stays free to
    # read the next frame — which is the only way a `cancel` can arrive while
    # the model is still streaming.
    turn: asyncio.Task[None] | None = None
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                incoming = TypeAdapter(ClientMessage).validate_json(raw)
            except ValidationError:
                ERRORS_TOTAL.labels(kind="invalid_message").inc()
                await websocket.send_text(
                    ErrorEvent(
                        message="invalid message, expected {type: user_message, content} "
                        "or {type: cancel}"
                    ).model_dump_json()
                )
                continue

            running = turn is not None and not turn.done()

            if isinstance(incoming, CancelRequest):
                if running and turn is not None:
                    logger.info("turn.cancel_requested", session_id=session_id)
                    turn.cancel()
                continue

            if running:
                # One turn at a time: the model is mid-answer and the history
                # it is working from would be wrong for a second question.
                await websocket.send_text(
                    ErrorEvent(
                        message="still answering the previous message — "
                        "stop it first, or wait for it to finish"
                    ).model_dump_json()
                )
                continue

            if not await _within_rate_limit(websocket, limiter, settings, session_id):
                continue

            turn = asyncio.create_task(
                _handle_turn(
                    websocket,
                    store,
                    memory,
                    agent,
                    requested_backend,
                    session_id,
                    incoming.content,
                    llm_model,
                )
            )
            # Nobody awaits a finished turn, so without this an escaping
            # exception would surface only as asyncio's "Task exception was
            # never retrieved" at GC time, detached from the session.
            turn.add_done_callback(_log_turn_task_result)
    except WebSocketDisconnect:
        logger.info("ws.disconnected", session_id=session_id)
        return
    finally:
        # Never leave a turn running against a socket nobody is reading.
        if turn is not None and not turn.done():
            turn.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await turn


async def _within_rate_limit(
    websocket: WebSocket, limiter: RateLimiter, settings: Settings, session_id: str
) -> bool:
    """Check the turn budget, reporting a refusal to the client.

    Deliberately before the turn starts: a runaway client then costs one Redis
    round trip instead of an LLM call.
    """
    decision = await limiter.check(
        "turns", session_id, limit=settings.rate_limit_turns_per_minute, window_seconds=60
    )
    if decision.allowed:
        return True

    ERRORS_TOTAL.labels(kind="rate_limited").inc()
    RATE_LIMITED_TOTAL.labels(bucket="turns").inc()
    logger.warning("turn.rate_limited", session_id=session_id, retry_after=decision.retry_after)
    await websocket.send_text(ErrorEvent(message=decision.message("messages")).model_dump_json())
    return False


def _log_turn_task_result(task: asyncio.Task[None]) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    # WebSocketDisconnect is the client leaving mid-turn: routine, and the
    # receive loop reports it already.
    if exc is not None and not isinstance(exc, WebSocketDisconnect):
        logger.error("turn.task_failed", exc_info=exc)


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
        cancelled = False
        failed = False
        try:
            with tracer.start_as_current_span("agent.turn") as span:
                span.set_attribute("session.id", session_id)
                span.set_attribute("turn.id", recorder.turn_id)
                span.set_attribute("agent.backend", backend)

                # Bounded view: rolling summary + recent turns (full transcript in Redis)
                history = await memory.context_for(session_id)
                await store.append(session_id, ChatMessage(role="user", content=user_message))

                async for event in agent.run(history=history, user_message=user_message):
                    if isinstance(event, FinalEvent):
                        # Guard here, not in a backend: all three funnel through
                        # this loop, and the UI replaces the streamed text with
                        # `final.content`, so correcting the event corrects what
                        # the user actually reads and what history keeps.
                        event = event.model_copy(
                            update={
                                "content": correct_unsupported_action_claims(
                                    event.content, tools_used=recorder.tool_calls
                                )
                            }
                        )
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
        except asyncio.CancelledError:
            # The user pressed Stop. Everything streamed so far is real and
            # stays on screen; the tokens were spent and still get counted.
            # `agent.run` is a generator, so leaving this block closes it and
            # its own finally-blocks end the spans and release the LLM stream.
            cancelled = True
            # Swallowing a CancelledError leaves the task's `cancelling` count
            # raised, which would make an enclosing TaskGroup or timeout treat
            # this task as still-cancelling. We consumed the request, so we
            # balance the count.
            if (task := asyncio.current_task()) is not None:
                task.uncancel()
            CANCELLED_TOTAL.labels(backend=backend).inc()
            logger.info("turn.cancelled", answer_chars=recorder.answer_chars)
            # Persist the partial answer, otherwise the history holds a question
            # nobody replied to and the next turn re-answers it from scratch.
            if partial := recorder.streamed_text.strip():
                await store.append(
                    session_id,
                    ChatMessage(role="assistant", content=f"{partial} [stopped by the user]"),
                )
        except Exception as exc:
            kind, message = describe_llm_error(exc) or (
                "turn_exception",
                "server error — check server logs (is Redis/Qdrant running?)",
            )
            ERRORS_TOTAL.labels(kind=kind).inc()
            logger.exception("turn.failed", kind=kind)
            # Same reasoning as the stopped path: text the user already saw is
            # part of the conversation. Dropping it desynchronises the screen
            # from the history the next prompt is built from.
            if partial := recorder.streamed_text.strip():
                await store.append(
                    session_id,
                    ChatMessage(role="assistant", content=f"{partial} [answer interrupted]"),
                )
            await websocket.send_text(ErrorEvent(message=message).model_dump_json())
            # Falls through to the summary rather than returning: `turn` is the
            # frame that ends a turn, so a client can wait for exactly one of
            # them however the turn goes. It also keeps the spend visible — a
            # turn that died after two provider retries burned three prompts,
            # and dropping it here hid that cost precisely when it mattered.
            failed = True
        finally:
            current_turn_stats.reset(stats_token)

        summary = recorder.summary(cancelled=cancelled, failed=failed)
        TURNS_TOTAL.labels(backend=backend).inc()
        if not (cancelled or failed):
            # A stopped turn measures the user's patience and a failed one the
            # provider's timeout — neither is this system's latency, and both
            # would skew the p95 they land in.
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
