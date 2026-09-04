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
import re
from typing import TYPE_CHECKING

from fastapi import FastAPI
from opentelemetry.trace import SpanKind  # the API package, already loaded by telemetry.py

from assistant.config import Settings

if TYPE_CHECKING:
    from opentelemetry.sdk.trace.sampling import Sampler

logger = logging.getLogger(__name__)

# Requests that are pure machinery, never a user turn: Prometheus scrapes
# /metrics every 5 s, the UI polls /api/health every 10 s. Matched as regexes
# against the URL by the OpenTelemetry FastAPI instrumentation.
NOISY_PATHS = ("/metrics", "/api/health", "/healthz")

# Root spans that are the same machinery seen from the other side: the health
# check's own Qdrant call becomes a root span once its request span is
# excluded, and httpx instrumentation has no URL exclusion of its own. Any
# root span that is an HTTP call to local infrastructure is machinery, not a
# turn — a user turn's Qdrant calls are children of its WebSocket span.
NOISY_ROOT_SPANS = re.compile(
    r"^(?:[A-Z]+ )?(?:" + "|".join(re.escape(path) for path in NOISY_PATHS) + r")$"
    r"|^[A-Z]+ localhost/"
)
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_HOST_KEYS = ("server.address", "net.peer.name", "http.host")
_URL_KEYS = ("url.full", "http.url")


def _is_machinery(name: str, kind: object, attributes: object) -> bool:
    """Name says so, or — for HTTP *client* spans — the attributes do.

    httpx spans are created under a bare method name ("POST") and renamed
    only after the sampling decision, so the name alone would let the
    health check's Qdrant call through; its attributes point at localhost.
    Only client spans are judged by host: a *server* span for `/chat` also
    carries `server.address = 127.0.0.1`, and dropping it would drop every
    real turn of a locally served app — which is exactly what happened once.
    """
    if NOISY_ROOT_SPANS.search(name):
        return True
    if kind is not SpanKind.CLIENT:
        return False
    attrs = attributes if isinstance(attributes, dict) else {}
    if any(str(attrs.get(key, "")) in _LOCAL_HOSTS for key in _HOST_KEYS):
        return True
    return any(
        f"://{host}" in str(attrs.get(key, "")) for key in _URL_KEYS for host in _LOCAL_HOSTS
    )


def make_noise_sampler() -> "Sampler":
    """A head sampler that drops noisy root spans and honours the parent otherwise.

    Built lazily so the OpenTelemetry SDK is imported only when tracing is on.
    """
    from opentelemetry import trace
    from opentelemetry.sdk.trace.sampling import Decision, Sampler, SamplingResult

    class DropNoisyRootSpans(Sampler):
        def should_sample(  # type: ignore[override]  # OTel's signature is untyped
            self,
            parent_context,
            trace_id,
            name,
            kind=None,
            attributes=None,
            links=None,
            trace_state=None,
        ):
            parent = trace.get_current_span(parent_context).get_span_context()
            if parent.is_valid:
                sampled = (
                    Decision.RECORD_AND_SAMPLE if parent.trace_flags.sampled else Decision.DROP
                )
                return SamplingResult(sampled, attributes, trace_state)
            if _is_machinery(name, kind, attributes):
                return SamplingResult(Decision.DROP, None, trace_state)
            return SamplingResult(Decision.RECORD_AND_SAMPLE, attributes, trace_state)

        def get_description(self) -> str:
            return "DropNoisyRootSpans"

    return DropNoisyRootSpans()


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
            sampling=logfire.SamplingOptions(head=make_noise_sampler()),
            console=False,
        )
        # Prometheus scrapes /metrics every 5 s and the UI polls /api/health
        # every 10 s: instrumented, those alone were 1,000+ traces an hour in
        # every cloud dashboard, burying the real turns and spending quota.
        logfire.instrument_fastapi(app, excluded_urls=NOISY_PATHS)
        logfire.instrument_httpx()
        logfire.instrument_pydantic_ai()
    else:
        # No Logfire: install a plain OTel SDK provider feeding the exporters.
        # telemetry.tracer is a ProxyTracer, so it picks this up automatically.
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider

        provider = TracerProvider(
            resource=Resource.create({"service.name": "ai-workspace-assistant"}),
            sampler=make_noise_sampler(),
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
