"""Factory: settings -> the available agent backends.

All backends implement AgentBackend and receive the same tool registry, so
the WS layer can switch between them per session (`?backend=`). `langgraph`
plugs in here in Phase 6.
"""

from assistant.agent.backends.custom import CustomAgent
from assistant.agent.backends.langgraph import LangGraphAgent
from assistant.agent.backends.pydantic_ai import PydanticAIAgent, build_pydantic_model
from assistant.agent.base import AgentBackend
from assistant.agent.tools import ToolRegistry
from assistant.config import Settings
from assistant.llm.client import LLMClient


def build_agents(
    settings: Settings, llm: LLMClient, tools: ToolRegistry | None = None
) -> dict[str, AgentBackend]:
    return {
        "custom": CustomAgent(llm=llm, system_prompt=settings.system_prompt, tools=tools),
        "pydantic_ai": PydanticAIAgent(
            model=build_pydantic_model(settings),
            system_prompt=settings.system_prompt,
            tools=tools,
        ),
        "langgraph": LangGraphAgent(llm=llm, system_prompt=settings.system_prompt, tools=tools),
    }
