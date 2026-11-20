"""LangGraph-оркестрация агента Melke: узел Agent, узел инструментов, структурированный ответ."""

import json
import logging
from typing import Any

from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.graph.state import CompiledStateGraph

from config import Settings, get_settings
from models import ActionType, AgentAction, AgentResponse, ResponseStatus, ToolStatus
from prompts import SYSTEM_PROMPT
from state import ManagerHandoff, State, User
from tools import TOOLS

logger = logging.getLogger(__name__)

FALLBACK_MESSAGE = (
    "Извините, не удалось сформировать ответ. Попробуйте переформулировать вопрос "
    "или я передам обращение менеджеру."
)


def build_model(settings: Settings) -> Runnable:
    """DeepSeek через OpenAI-совместимый API с привязанными инструментами."""
    return ChatOpenAI(
        api_key=settings.deepseek_api,
        base_url=settings.deepseek_base_url,
        model=settings.deepseek_model,
        temperature=settings.deepseek_temperature,
        timeout=settings.deepseek_timeout,
        max_retries=0,
    ).bind_tools(TOOLS)


class Agent:
    """Диалог с клиентом. Состояние хранится в памяти процесса (MVP)."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._model = build_model(self._settings)
        self._graph: CompiledStateGraph = self._build_graph()
        self._state: State = {
            "user": User(id_user=1),
            "messages": [],
            "last_user_message": None,
            "candidate_products": [],
            "manager_handoff": ManagerHandoff(),
            "calculator_url": None,
        }
        self.last_response: AgentResponse | None = None

    # ---------- граф ----------

    def _build_graph(self) -> CompiledStateGraph:
        graph = StateGraph(State)
        graph.add_node("agent", self._agent_node)
        graph.add_node("tools", ToolNode(TOOLS))
        graph.add_edge(START, "agent")
        graph.add_conditional_edges("agent", _route, {"tools": "tools", "end": END})
        graph.add_edge("tools", "agent")
        return graph.compile()

    async def _agent_node(self, state: State) -> dict[str, Any]:
        history = _trim(state["messages"], self._settings.history_messages_limit)
        response = await self._model.ainvoke(
            [SystemMessage(content=SYSTEM_PROMPT), *history]
        )
        return {"messages": [response]}

    # ---------- диалог ----------

    async def ask(self, text: str) -> AgentResponse:
        """Обрабатывает сообщение клиента и возвращает структурированный ответ."""
        history = _trim(self._state["messages"], self._settings.history_messages_limit)
        input_state: State = {
            **self._state,
            "messages": [*history, HumanMessage(content=text)],
            "last_user_message": text,
        }
        turn_start = len(input_state["messages"]) - 1

        result = await self._graph.ainvoke(input_state)
        response = _build_response(result, turn_start)
        self._state = {
            **result,
            "messages": _trim(result["messages"], self._settings.history_messages_limit),
        }
        self.last_response = response
        return response

    def state_dump(self) -> dict[str, Any]:
        """Состояние диалога для команды /state."""
        return {
            "user": self._state["user"].model_dump(mode="json"),
            "handoff": self._state["manager_handoff"].model_dump(mode="json"),
            "calculator_url": self._state["calculator_url"],
            "last_user_message": self._state["last_user_message"],
            "messages": [
                {
                    "type": type(message).__name__,
                    "content": _text(message.content)[:300],
                    "tools": [call["name"] for call in getattr(message, "tool_calls", None) or []],
                }
                for message in self._state["messages"]
            ],
        }


def _route(state: State) -> str:
    last = state["messages"][-1]
    return "tools" if isinstance(last, AIMessage) and last.tool_calls else "end"


def _trim(messages: list[AnyMessage], limit: int) -> list[AnyMessage]:
    """Оставляет последние `limit` сообщений, начиная с сообщения клиента."""
    if len(messages) <= limit:
        return list(messages)
    trimmed = list(messages[-limit:])
    while trimmed and not isinstance(trimmed[0], HumanMessage):
        trimmed.pop(0)
    return trimmed


def _build_response(state: State, turn_start: int) -> AgentResponse:
    turn = state["messages"][turn_start:]
    message = next(
        (
            _text(item.content).strip()
            for item in reversed(turn)
            if isinstance(item, AIMessage) and not item.tool_calls and _text(item.content).strip()
        ),
        "",
    )
    status = _turn_status(turn)
    if not message:
        message, status = FALLBACK_MESSAGE, ResponseStatus.ERROR

    actions: list[AgentAction] = []
    calculator_requested = any(
        isinstance(item, ToolMessage) and item.name == "get_calculator_link" for item in turn
    )
    if calculator_requested and state["calculator_url"]:
        actions.append(
            AgentAction(type=ActionType.CALCULATOR_LINK, url=state["calculator_url"])
        )

    return AgentResponse(
        message=message,
        status=status,
        actions=actions,
        handoff=state["manager_handoff"],
    )


def _turn_status(messages: list[AnyMessage]) -> ResponseStatus:
    """Статус обработки запроса по результатам инструментов текущего хода."""
    statuses: list[str] = []
    for message in messages:
        if not isinstance(message, ToolMessage):
            continue
        try:
            payload = json.loads(_text(message.content))
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and isinstance(payload.get("status"), str):
            statuses.append(payload["status"])

    if ToolStatus.ERROR.value in statuses:
        return ResponseStatus.ERROR
    if statuses and all(status == ToolStatus.NO_RESULTS.value for status in statuses):
        return ResponseStatus.NO_RESULTS
    return ResponseStatus.SUCCESS


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            part.get("text", "") if isinstance(part, dict) else str(part) for part in content
        )
    return str(content)
