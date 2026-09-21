"""LangGraph ReAct loop. Built once and cached; the LLM client is rebuilt only
when provider/model settings change."""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Annotated, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from geoagent.agent.prompts import SYSTEM_PROMPT
from geoagent.agent.tools import ALL_TOOLS
from geoagent.config import settings

log = logging.getLogger(__name__)

_OPENAI_COMPATIBLE = {
    "groq": "https://api.groq.com/openai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "openai": None,
}


def build_llm(provider: str | None = None, model: str | None = None):
    provider = (provider or settings.LLM_PROVIDER).lower()
    model = model or settings.LLM_MODEL

    if provider == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI

        if not settings.GOOGLE_API_KEY:
            raise RuntimeError("GOOGLE_API_KEY is not set")
        return ChatGoogleGenerativeAI(model=model, temperature=settings.LLM_TEMPERATURE,
                                      google_api_key=settings.GOOGLE_API_KEY)

    if provider not in _OPENAI_COMPATIBLE:
        raise RuntimeError(f"Unsupported LLM_PROVIDER '{provider}'")

    from langchain_openai import ChatOpenAI

    key = {"groq": settings.GROQ_API_KEY, "openrouter": settings.OPENROUTER_API_KEY,
           "openai": settings.OPENAI_API_KEY}[provider]
    if not key:
        raise RuntimeError(f"No API key configured for provider '{provider}'")
    kwargs = dict(model=model, temperature=settings.LLM_TEMPERATURE, api_key=key, timeout=120, max_retries=2)
    if _OPENAI_COMPATIBLE[provider]:
        kwargs["base_url"] = _OPENAI_COMPATIBLE[provider]
    return ChatOpenAI(**kwargs)


class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]


@lru_cache(maxsize=4)
def build_graph(provider: str, model: str):
    llm = build_llm(provider, model).bind_tools(ALL_TOOLS, tool_choice="auto", parallel_tool_calls=False)

    def call_model(state: AgentState):
        messages = state["messages"]
        if not isinstance(messages[0], SystemMessage):
            messages = [SystemMessage(content=SYSTEM_PROMPT), *messages]
        return {"messages": [llm.invoke(messages)]}

    def route(state: AgentState) -> str:
        last = state["messages"][-1]
        return "tools" if getattr(last, "tool_calls", None) else END

    g = StateGraph(AgentState)
    g.add_node("agent", call_model)
    g.add_node("tools", ToolNode(ALL_TOOLS))
    g.set_entry_point("agent")
    g.add_conditional_edges("agent", route, {"tools": "tools", END: END})
    g.add_edge("tools", "agent")
    return g.compile()


def run_agent(query: str, history: list[BaseMessage] | None = None,
              provider: str | None = None, model: str | None = None) -> tuple[str, list[str]]:
    """Return (final answer, run ids touched by tools)."""
    if not settings.llm_configured and not provider:
        raise RuntimeError("No LLM API key configured. Set GROQ_API_KEY (or another provider) in .env")

    graph = build_graph(provider or settings.LLM_PROVIDER, model or settings.LLM_MODEL)
    messages: list[BaseMessage] = list(history or []) + [HumanMessage(content=query)]
    final = None
    for event in graph.stream({"messages": messages}, {"recursion_limit": 12}, stream_mode="values"):
        final = event

    if not final:
        return "No response generated.", []

    run_ids: list[str] = []
    for m in final["messages"]:
        if getattr(m, "type", "") == "tool" and getattr(m, "name", "") == "monitor_aoi":
            try:
                import json
                rid = json.loads(m.content).get("id")
                if rid:
                    run_ids.append(rid)
            except Exception:  # noqa: BLE001
                pass

    last = final["messages"][-1]
    text = last.content if isinstance(last, AIMessage) else str(last)
    if isinstance(text, list):  # some providers return content blocks
        text = "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in text)
    return text, run_ids
