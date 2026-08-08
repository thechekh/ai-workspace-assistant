"""Provider-error classification, independent of any transport.

Turns whatever a provider (or an agent framework wrapping one) raised into
`(metric_kind, user_facing_message)`. It lives in the LLM package rather than
the WebSocket layer because it is knowledge about *providers*, and any
entrypoint — WS, HTTP, a CLI, a batch job — needs the same mapping.

Classification is by `status_code` duck-typing rather than isinstance, so one
implementation covers openai's `APIStatusError` and pydantic-ai's
`ModelHTTPError` alike, and it walks the `__cause__`/`__context__` chain
because agent frameworks re-raise provider errors wrapped in their own.
"""

from openai import APIConnectionError

from assistant.llm.client import is_tool_use_failure

# Depth limit on the exception-chain walk; deep chains are pathological.
_MAX_CHAIN_DEPTH = 10


def describe_llm_error(exc: BaseException) -> tuple[str, str] | None:
    """(metric kind, user-facing message), or None if not provider-shaped.

    Returning None is meaningful: the caller should fall back to its own
    generic message rather than guess that a bug is an LLM problem.
    """
    current: BaseException | None = exc
    for _ in range(_MAX_CHAIN_DEPTH):
        if current is None:
            return None

        if is_tool_use_failure(current):
            return (
                "tool_use_failed",
                "The model failed to generate a valid tool call (a known llama "
                "flake, already retried) — send the message again or rephrase.",
            )

        status = getattr(current, "status_code", None)
        if isinstance(status, int):
            detail = str(getattr(current, "message", None) or current)[:200]
            if status == 429:
                # Pass the provider's own text through — it says WHICH limit
                # (per-minute vs per-day) and how long to wait; our guess would
                # mislead (a daily cap won't clear "in a few seconds").
                return (
                    "rate_limited",
                    f"LLM rate limit hit (429). Provider says: {detail}"
                    if detail
                    else "LLM rate limit hit (429) — wait and retry, or switch "
                    "ASSISTANT_LLM_MODEL to a model with remaining quota.",
                )
            if status in (401, 403):
                return (
                    "auth_failed",
                    "LLM authentication failed — check ASSISTANT_LLM_API_KEY.",
                )
            if status == 404:
                return (
                    "model_unavailable",
                    f"Model not available — check ASSISTANT_LLM_MODEL. Provider says: {detail}",
                )
            if status >= 500:
                return "provider_error", f"LLM provider error ({status}) — try again shortly."
            return "llm_bad_request", f"LLM rejected the request ({status}): {detail}"

        if isinstance(current, APIConnectionError):  # includes APITimeoutError
            return (
                "provider_unreachable",
                "Cannot reach the LLM provider — check the network and ASSISTANT_LLM_BASE_URL.",
            )

        current = current.__cause__ or current.__context__
    return None
