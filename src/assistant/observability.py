"""Tracing destinations over one OpenTelemetry pipeline.

Three destinations, all optional, all fed by the same manual spans
(`agent.turn` / `llm.step` / `tool.execute` / `rag.retrieve` from
assistant.telemetry):

- ASSISTANT_OTLP_ENDPOINT -> local Jaeger, zero accounts
  (`docker compose --profile observability up`, UI at :16686)
- ASSISTANT_LOGFIRE_TOKEN -> Logfire cloud (app view: requests, WS lifecycle,
  LLM HTTP calls, pydantic-ai agent runs — adds FastAPI/httpx auto-instrumentation)
- ASSISTANT_LANGFUSE_*    -> Langfuse OTLP endpoint (LLM view: generations,
  token costs, evals)

Any combination can be active at once — one instrumentation, specialized
backends. Without credentials this module is fully inert: nothing heavy is
imported, no global OTel state (tests and no-env dev stay clean), and the
tracer in assistant.telemetry remains a no-op.
"""

import base64
import logging

from fastapi import FastAPI

from assistant.config import Settings

logger = logging.getLogger(__name__)


def configure_observability(app: FastAPI, settings: Settings) -> None:
    has_otlp = settings.otlp_endpoint is not None
    has_logfire = settings.logfire_token is not None
    has_langfuse = (
        settings.langfuse_public_key is not None and settings.langfuse_secret_key is not None
    )
    if not (has_otlp or has_logfire or has_langfuse):
        logger.info("tracing disabled — no OTLP/Logfire/Langfuse destination configured")
        return

    # Heavy imports only when a destination is actually enabled.
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    processors: list[BatchSpanProcessor] = []
    if has_otlp:
        endpoint = (settings.otlp_endpoint or "").rstrip("/")
        processors.append(BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces")))
    if (langfuse_secret := settings.langfuse_secret_key) is not None and has_langfuse:
        credentials = f"{settings.langfuse_public_key}:{langfuse_secret.get_secret_value()}"
        auth = base64.b64encode(credentials.encode()).decode()
        processors.append(
            BatchSpanProcessor(
                OTLPSpanExporter(
                    endpoint=f"{settings.langfuse_host.rstrip('/')}/api/public/otel/v1/traces",
                    headers={"Authorization": f"Basic {auth}"},
                )
            )
        )

    if has_logfire:
        import logfire

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
    else:
        # No Logfire: install a plain OTel SDK provider feeding the exporters.
        # telemetry.tracer is a ProxyTracer, so it picks this up automatically.
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider

        provider = TracerProvider(
            resource=Resource.create({"service.name": "ai-workspace-assistant"})
        )
        for processor in processors:
            provider.add_span_processor(processor)
        trace.set_tracer_provider(provider)

    logger.info(
        "tracing configured (otlp=%s, logfire=%s, langfuse=%s)",
        has_otlp,
        has_logfire,
        has_langfuse,
    )
