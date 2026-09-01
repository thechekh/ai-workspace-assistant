"""The tool contract and the single execution seam.

`Tool.run` is where every call — native or MCP, from any backend — gets its
span, metrics, structured log, duplicate guard, and crash isolation. The
concrete tools live in sibling modules (`search_docs.py`, `fetch.py`).
"""

import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import structlog

from assistant.llm.client import ToolSpec
from assistant.telemetry import TOOL_CALLS_TOTAL, TOOL_SECONDS, current_turn_stats, tracer

logger = structlog.get_logger("assistant.tools")

ToolHandler = Callable[[dict[str, object]], Awaitable[str]]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, object]  # JSON schema for the arguments
    handler: ToolHandler

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(name=self.name, description=self.description, parameters=self.parameters)

    async def run(self, arguments: dict[str, object]) -> str:
        """Execute with telemetry; a crash becomes an error *result*, never an exception."""
        # Duplicate guard: the same call twice in one turn returns a pointer to
        # the earlier result instead of re-executing (models sometimes loop).
        stats = current_turn_stats.get()
        if stats is not None:
            key = (self.name, json.dumps(arguments, sort_keys=True, default=str))
            if key in stats.seen_tool_calls:
                TOOL_CALLS_TOTAL.labels(tool=self.name, status="duplicate").inc()
                logger.warning("tool.duplicate_call", tool=self.name)
                return (
                    f"error: duplicate call — {self.name} already ran with exactly these "
                    "arguments in this turn. Use the result you already received above; "
                    "do not repeat the call."
                )
            stats.seen_tool_calls.add(key)

        start = time.perf_counter()
        status = "ok"
        with tracer.start_as_current_span("tool.execute") as span:
            span.set_attribute("tool.name", self.name)
            try:
                result = await self.handler(dict(arguments))
            except Exception as exc:  # a tool crash must not kill the agent loop
                status = "crash"
                result = f"error: tool {self.name!r} failed: {exc}"
                logger.warning("tool.crashed", tool=self.name, exc_info=True)
            if status == "ok" and result.startswith("error:"):
                status = "error"
            span.set_attribute("tool.status", status)
            span.set_attribute("tool.result_chars", len(result))
        duration = time.perf_counter() - start
        TOOL_SECONDS.labels(tool=self.name).observe(duration)
        TOOL_CALLS_TOTAL.labels(tool=self.name, status=status).inc()
        logger.info(
            "tool.executed",
            tool=self.name,
            status=status,
            duration_ms=round(duration * 1000),
            result_chars=len(result),
        )
        return result


class ToolRegistry:
    def __init__(self, tools: list[Tool] | None = None) -> None:
        self._tools: dict[str, Tool] = {tool.name: tool for tool in tools or []}

    def __len__(self) -> int:
        return len(self._tools)

    @property
    def tools(self) -> list[Tool]:
        return list(self._tools.values())

    @property
    def specs(self) -> list[ToolSpec]:
        return [tool.spec for tool in self._tools.values()]

    async def execute(self, name: str, arguments: dict[str, object]) -> str:
        tool = self._tools.get(name)
        if tool is None:
            # Fixed label, NOT the requested name: that name comes from the
            # model, and a hallucinated one would add a Prometheus time series
            # that never goes away. The real name is in the log line instead.
            TOOL_CALLS_TOTAL.labels(tool="<unregistered>", status="unknown").inc()
            logger.warning("tool.unknown", tool=name)
            return f"error: unknown tool {name!r}"
        return await tool.run(arguments)
