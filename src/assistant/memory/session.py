"""Short-term memory: Redis-backed per-session conversation history.

Each session is a Redis list of JSON-serialized ChatMessage entries with a
TTL that refreshes on every append, plus a rolling-summary record used by
ConversationMemory ("summary text" + how many messages it covers).
"""

import json
import uuid

from redis.asyncio import Redis

from assistant.agent.base import ChatMessage


class SessionStore:
    def __init__(self, redis: Redis, ttl_seconds: int) -> None:
        self._redis = redis
        self._ttl = ttl_seconds

    @staticmethod
    def new_session_id() -> str:
        return uuid.uuid4().hex

    @staticmethod
    def _key(session_id: str) -> str:
        return f"session:{session_id}:messages"

    async def history(self, session_id: str) -> list[ChatMessage]:
        raw = await self._redis.lrange(self._key(session_id), 0, -1)
        return [ChatMessage.model_validate_json(item) for item in raw]

    async def append(self, session_id: str, message: ChatMessage) -> None:
        key = self._key(session_id)
        await self._redis.rpush(key, message.model_dump_json())
        await self._redis.expire(key, self._ttl)

    @staticmethod
    def _turns_key(session_id: str) -> str:
        return f"session:{session_id}:turns"

    async def append_turn(self, session_id: str, record: dict[str, object]) -> None:
        """Audit trail: one record per turn (summary + event timeline), capped at 50."""
        key = self._turns_key(session_id)
        await self._redis.rpush(key, json.dumps(record))
        await self._redis.ltrim(key, -50, -1)
        await self._redis.expire(key, self._ttl)

    async def turns(self, session_id: str) -> list[dict[str, object]]:
        raw = await self._redis.lrange(self._turns_key(session_id), 0, -1)
        return [json.loads(item) for item in raw]

    @staticmethod
    def _summary_key(session_id: str) -> str:
        return f"session:{session_id}:summary"

    async def summary(self, session_id: str) -> tuple[str, int]:
        """The rolling summary and how many history messages it covers."""
        raw = await self._redis.get(self._summary_key(session_id))
        if not raw:
            return "", 0
        data = json.loads(raw)
        return str(data.get("text", "")), int(data.get("covered", 0))

    async def set_summary(self, session_id: str, text: str, covered: int) -> None:
        key = self._summary_key(session_id)
        await self._redis.set(key, json.dumps({"text": text, "covered": covered}))
        await self._redis.expire(key, self._ttl)
