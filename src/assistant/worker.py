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
async def reindex_docs(corpus: str | None = None) -> int:
    """Re-ingest a folder of Markdown, if one is configured.

    Documents added through POST /api/documents live in Qdrant and need no
    re-indexing, so with no ASSISTANT_CORPUS_DIR this is a no-op rather than
    an error — the nightly schedule should not fail an unconfigured install.
    """
    settings = Settings()
    corpus_dir = Path(corpus) if corpus else settings.corpus_dir
    if corpus_dir is None:
        logger.info("reindex skipped — no ASSISTANT_CORPUS_DIR configured")
        return 0
    count = await ingest(corpus_dir, settings)
    logger.info("reindexed %s chunks from %s", count, corpus_dir)
    return count


scheduler = TaskiqScheduler(broker, [LabelScheduleSource(broker)])
