"""Short-term memory: Redis-backed per-session conversation history.

Each session is a Redis list of JSON-serialized ChatMessage entries with a
TTL that refreshes on every append, plus a rolling-summary record used by
ConversationMemory ("summary text" + how many messages it covers).

Sessions are also indexed in a sorted set scored by last activity, so the UI
can list recent conversations without `KEYS`/`SCAN` over the keyspace — the
one pattern that gets slower exactly as a deployment gets busier.
"""

import json
import time
import uuid

from redis.asyncio import Redis

from assistant.agent.base import ChatMessage
from assistant.api.schemas import SessionSummary, TurnRecord

# Audit trail depth per session — enough to debug a conversation, bounded
# so a long session cannot grow without limit.
_MAX_AUDIT_TURNS = 50

# How much of the opening question to keep as a sidebar label.
_PREVIEW_CHARS = 80

_INDEX_KEY = "sessions:index"


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
        pipe = self._redis.pipeline(transaction=True)
        pipe.rpush(key, message.model_dump_json())
        pipe.expire(key, self._ttl)
        # Same round trip: keep the recency index in step with the data. A
        # separate call could leave a session listed but already expired.
        pipe.zadd(_INDEX_KEY, {session_id: time.time()})
        await pipe.execute()

    async def recent(self, limit: int = 30) -> list[SessionSummary]:
        """The most recently active sessions, newest first.

        Sorted-set members outlive the keys they point at (Redis expires keys,
        not set entries), so entries older than the TTL are dropped on read and
        any session whose history is already gone is skipped.
        """
        # ZREVRANGE takes an inclusive *end index*, where negatives count from
        # the end — so limit=0 would ask for 0..-1, i.e. everything, and turn a
        # cap into its opposite. Clamp before the arithmetic, not after.
        if limit <= 0:
            return []
        cutoff = time.time() - self._ttl
        await self._redis.zremrangebyscore(_INDEX_KEY, 0, cutoff)
        # `withscores=True` returns (member, score) pairs; redis-py types the
        # reply loosely because it depends on the arguments.
        entries: list[tuple[str, float]] = await self._redis.zrevrange(  # pyright: ignore[reportAssignmentType]
            _INDEX_KEY, 0, limit - 1, withscores=True
        )
        if not entries:
            return []

        # One round trip for every session's first message + length, rather
        # than two calls per session.
        pipe = self._redis.pipeline(transaction=False)
        for session_id, _ in entries:
            pipe.lindex(self._key(session_id), 0)
            pipe.llen(self._key(session_id))
        replies = await pipe.execute()

        sessions: list[SessionSummary] = []
        for index, (session_id, updated_at) in enumerate(entries):
            first_raw, length = replies[index * 2], replies[index * 2 + 1]
            if not length:
                continue  # history expired; the index entry is stale
            preview = ""
            if first_raw:
                preview = ChatMessage.model_validate_json(first_raw).content
            sessions.append(
                SessionSummary(
                    session_id=session_id,
                    updated_at=updated_at,
                    messages=int(length),
                    preview=preview[:_PREVIEW_CHARS],
                )
            )
        return sessions

    async def forget(self, session_id: str) -> bool:
        """Delete a conversation and everything recorded about it."""
        removed = await self._redis.delete(
            self._key(session_id),
            self._turns_key(session_id),
            self._summary_key(session_id),
        )
        await self._redis.zrem(_INDEX_KEY, session_id)
        return bool(removed)

    @staticmethod
    def _turns_key(session_id: str) -> str:
        return f"session:{session_id}:turns"

    async def append_turn(self, session_id: str, record: TurnRecord) -> None:
        """Audit trail: one record per turn (summary + event timeline), capped at 50."""
        key = self._turns_key(session_id)
        await self._redis.rpush(key, record.model_dump_json())
        await self._redis.ltrim(key, -_MAX_AUDIT_TURNS, -1)
        await self._redis.expire(key, self._ttl)

    async def turns(self, session_id: str) -> list[TurnRecord]:
        raw = await self._redis.lrange(self._turns_key(session_id), 0, -1)
        return [TurnRecord.model_validate_json(item) for item in raw]

    async def turn(self, session_id: str, turn_id: str) -> TurnRecord | None:
        """One turn by id — what the UI's "details" panel actually needs."""
        for record in await self.turns(session_id):
            if record.turn_id == turn_id:
                return record
        return None

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
