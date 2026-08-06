"""Logfire + Langfuse over one OpenTelemetry pipeline.

Without credentials this module is fully inert — nothing heavy is imported,
no network calls, no global OTel state (tests and no-env dev stay clean).

With ASSISTANT_LOGFIRE_TOKEN set, spans go to Logfire (app view: requests,
WS lifecycle, LLM HTTP calls, pydantic-ai agent runs). With the
ASSISTANT_LANGFUSE_* keys set, the same spans are additionally exported to
Langfuse's OTLP endpoint (LLM view: generations, token costs, evals). Both
can be active at once — one instrumentation, two specialized backends.
"""

import base64
import logging

from fastapi import FastAPI

from assistant.config import Settings

logger = logging.getLogger(__name__)


def configure_observability(app: FastAPI, settings: Settings) -> None:
    has_logfire = settings.logfire_token is not None
    has_langfuse = (
        settings.langfuse_public_key is not None and settings.langfuse_secret_key is not None
    )
    if not (has_logfire or has_langfuse):
        logger.info("observability disabled — no Logfire/Langfuse credentials configured")
        return

    # Heavy imports only when actually enabled.
    import logfire

    processors = []
    if has_langfuse:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        assert settings.langfuse_secret_key is not None
        credentials = (
            f"{settings.langfuse_public_key}:{settings.langfuse_secret_key.get_secret_value()}"
        )
        auth = base64.b64encode(credentials.encode()).decode()
        exporter = OTLPSpanExporter(
            endpoint=f"{settings.langfuse_host.rstrip('/')}/api/public/otel/v1/traces",
            headers={"Authorization": f"Basic {auth}"},
        )
        processors.append(BatchSpanProcessor(exporter))

    logfire.configure(
        token=settings.logfire_token.get_secret_value() if settings.logfire_token else None,
        send_to_logfire="if-token-present",
        service_name="ai-workspace-assistant",
        additional_span_processors=processors or None,
        console=False,
    )
    logfire.instrument_fastapi(app)
    logfire.instrument_httpx()
    logfire.instrument_pydantic_ai()
    logger.info("observability configured (logfire=%s, langfuse=%s)", has_logfire, has_langfuse)
