import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from assistant.agent.base import AgentBackend, ChatMessage, ErrorEvent, FinalEvent
from assistant.api.schemas import SessionStarted, UserMessage
from assistant.memory.conversation import ConversationMemory
from assistant.memory.session import SessionStore

router = APIRouter()
logger = logging.getLogger(__name__)


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

    # ?backend= picks the runtime for this connection (side-by-side comparison);
    # unknown/absent values fall back to the configured default.
    requested_backend = websocket.query_params.get("backend") or default_backend
    agent = agents.get(requested_backend) or agents[default_backend]

    # Reconnecting clients pass ?session_id=... to resume their history.
    session_id = websocket.query_params.get("session_id") or SessionStore.new_session_id()
    await websocket.send_text(SessionStarted(session_id=session_id).model_dump_json())

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                incoming = UserMessage.model_validate_json(raw)
            except ValidationError:
                await websocket.send_text(
                    ErrorEvent(
                        message="invalid message, expected {type: user_message, content}"
                    ).model_dump_json()
                )
                continue

            try:
                # Bounded view: rolling summary + recent turns (full transcript stays in Redis)
                history = await memory.context_for(session_id)
                await store.append(session_id, ChatMessage(role="user", content=incoming.content))
                async for event in agent.run(history=history, user_message=incoming.content):
                    await websocket.send_text(event.model_dump_json())
                    if isinstance(event, FinalEvent):
                        await store.append(
                            session_id, ChatMessage(role="assistant", content=event.content)
                        )
            except Exception:
                # An LLM/tool/infra failure must not kill the socket — report and keep serving.
                logger.exception("message handling failed (session %s)", session_id)
                await websocket.send_text(
                    ErrorEvent(
                        message="server error — check server logs (is Redis/Qdrant running?)"
                    ).model_dump_json()
                )
    except WebSocketDisconnect:
        return
