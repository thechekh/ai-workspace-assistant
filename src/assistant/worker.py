"""taskiq background worker: document ingestion + nightly re-index.

Run (needs real Redis; the API falls back to inline execution on fakeredis://):

    uv run taskiq worker assistant.worker:broker
    uv run taskiq scheduler assistant.worker:scheduler

taskiq-fastapi (DI sharing) is deliberately not used — the task builds its
own clients from Settings, so the worker has no FastAPI dependency.
"""

import logging
from pathlib import Path

from taskiq import TaskiqScheduler
from taskiq.schedule_sources import LabelScheduleSource
from taskiq_redis import ListQueueBroker

from assistant.config import Settings
from assistant.rag.ingest import ingest

logger = logging.getLogger(__name__)

_settings = Settings()
broker = ListQueueBroker(url=_settings.redis_url)


@broker.task(schedule=[{"cron": "0 3 * * *"}])  # nightly re-index at 03:00
async def reindex_docs(corpus: str = "docs_corpus") -> int:
    settings = Settings()
    count = await ingest(Path(corpus), settings)
    logger.info("reindexed %s chunks from %s", count, corpus)
    return count


scheduler = TaskiqScheduler(broker, [LabelScheduleSource(broker)])
