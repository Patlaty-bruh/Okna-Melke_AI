"""Инструменты агента: каталог, база знаний, калькулятор, менеджер, профиль клиента."""

import asyncio
import json
import logging
import re
from collections.abc import Callable
from contextlib import suppress
from functools import lru_cache
from typing import Annotated, Any, Literal, TypeVar

import psycopg
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import InjectedState
from langgraph.types import Command
from pydantic import ValidationError

from catalog import get_catalog
from config import get_settings
from models import (
    KNOWLEDGE_CATEGORIES,
    KNOWLEDGE_CATEGORY_ALIASES,
    KnowledgeHit,
    ProductSearchQuery,
    ToolStatus,
)
from prompts import PRODUCT_QUERY_INSTRUCTION
from rag.embeddings import Embedder, get_embedder
from rag.search import HybridSearcher
from rag.store import VectorStore
from state import BudgetLevel, ManagerHandoffStatus, State

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _criteria_model() -> ChatOpenAI:
    """Модель для формирования критериев поиска по каталогу (§5.1)."""
    settings = get_settings()
    return ChatOpenAI(
        api_key=settings.deepseek_api,
        base_url=settings.deepseek_base_url,
        model=settings.deepseek_model,
        temperature=0,
        timeout=settings.deepseek_timeout,
        max_retries=0,
    )


@lru_cache(maxsize=1)
def _embedder() -> Embedder:
    """Модель эмбеддингов создаётся один раз на процесс.

    Кэшируется отдельно от поисковика: при потере соединения с БД модель перезагружать незачем.
    """
    return get_embedder(get_settings())


@lru_cache(maxsize=1)
def _searcher() -> HybridSearcher:
    """Гибридный поиск создаётся один раз: модель эмбеддингов и подключение к БД."""
    settings = get_settings()
    return HybridSearcher(settings, _embedder(), VectorStore(settings))


_DbResult = TypeVar("_DbResult")


def _reset_db_caches() -> None:
    """Сбрасывает кэш подключений к PostgreSQL.

    После перезапуска или пересоздания контейнера БД кэшированные Catalog и HybridSearcher
    держат мёртвое соединение, и поиск перестаёт работать до перезапуска приложения.
    """
    if get_catalog.cache_info().currsize:
        with suppress(Exception):
            get_catalog().close()
        get_catalog.cache_clear()
    if _searcher.cache_info().currsize:
        with suppress(Exception):
            _searcher().close()
        _searcher.cache_clear()


def _run_db_call(call: Callable[[], _DbResult]) -> _DbResult:
    """Выполняет обращение к БД, пересоздавая подключение при потере соединения."""
    try:
        return call()
    except (psycopg.OperationalError, psycopg.InterfaceError):
        logger.warning("Соединение с PostgreSQL потеряно, пересоздаю подключение")
        _reset_db_caches()
        return call()


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _extract_json(text: str) -> dict[str, Any] | None:
    """Достаёт JSON-объект из ответа модели: снимает Markdown и лишний текст."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        payload = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


async def _extract_criteria(query: str) -> ProductSearchQuery | None:
    """Отдельный вызов LLM: свободный запрос -> критерии поиска, проверенные Pydantic.

    Повторных попыток нет: при ошибке поиск не выполняется (§12).
    """
    try:
        message = await _criteria_model().ainvoke(
            [
                SystemMessage(content=PRODUCT_QUERY_INSTRUCTION),
                HumanMessage(content=query),
            ]
        )
    except Exception:
        logger.exception("Не удалось получить критерии поиска от модели")
        return None

    payload = _extract_json(str(message.content))
    if payload is None:
        logger.warning("Модель вернула не JSON: %r", str(message.content)[:300])
        return None
    try:
        return ProductSearchQuery.model_validate(payload)
    except ValidationError as error:
        logger.warning("Критерии поиска не прошли проверку: %s", error)
        return None


def _normalize_categories(categories: list[str] | None) -> list[str] | None:
    """Оставляет допустимые категории базы знаний, подменяет отсутствующие псевдонимы."""
    if not categories:
        return None
    chosen: list[str] = []
    for category in categories:
        name = category.strip().lower()
        name = KNOWLEDGE_CATEGORY_ALIASES.get(name, name)
        if name in KNOWLEDGE_CATEGORIES and name not in chosen:
            chosen.append(name)
    return chosen or None


@tool
async def search_products(query: str) -> str:
    """Подбор продукции Melke из каталога: профильные системы, стеклопакеты, ламинация, фурнитура.

    Возвращает только подтверждённые характеристики из каталога. Готовых конфигураций окон,
    цен, совместимости профиля со стеклопакетами и фурнитурой в каталоге нет.
    """
    criteria = await _extract_criteria(query)
    if criteria is None:
        return _json(
            {
                "status": ToolStatus.ERROR.value,
                "reason": "Не удалось сформировать критерии поиска по каталогу.",
                "products": [],
            }
        )

    try:
        hits = await asyncio.to_thread(_run_db_call, lambda: get_catalog().search(criteria))
    except RuntimeError:
        logger.exception("Ошибка поиска по каталогу")
        return _json(
            {
                "status": ToolStatus.ERROR.value,
                "reason": "Каталог продукции сейчас недоступен.",
                "products": [],
            }
        )

    if not hits:
        return _json(
            {
                "status": ToolStatus.NO_RESULTS.value,
                "reason": "В каталоге нет записей с такими характеристиками.",
                "products": [],
            }
        )
    return _json(
        {
            "status": ToolStatus.SUCCESS.value,
            "products": [hit.model_dump(mode="json") for hit in hits],
        }
    )


@tool
async def search_knowledge(
    query: str,
    categories: list[str] | None = None,
    limit: int = 6,
) -> str:
    """Поиск по базе знаний Melke: о компании, технологиях, гарантии, монтаже, доставке,
    замере, заказе, эксплуатации, а также часто задаваемые вопросы.

    categories — необязательный фильтр по документам: company, products, technology, warranty,
    installation, maintenance, ordering, delivery, faq, documents.
    """
    settings = get_settings()
    result_limit = max(
        settings.knowledge_search_min_limit,
        min(limit, settings.knowledge_search_max_limit),
    )
    chosen = _normalize_categories(categories)

    try:
        hits = await asyncio.to_thread(
            _run_db_call, lambda: _searcher().search(query, result_limit, chosen)
        )
    except Exception:
        logger.exception("Ошибка поиска по базе знаний")
        return _json(
            {
                "status": ToolStatus.ERROR.value,
                "reason": "База знаний сейчас недоступна.",
                "chunks": [],
            }
        )

    if not hits:
        return _json(
            {
                "status": ToolStatus.NO_RESULTS.value,
                "reason": "В базе знаний нет подходящих фрагментов.",
                "chunks": [],
            }
        )

    chunks = [
        KnowledgeHit(
            text=hit.text,
            source_file=hit.source_file,
            section=hit.section,
            category=hit.category,
            source_url=hit.source_url,
        )
        for hit in hits
    ]
    return _json(
        {
            "status": ToolStatus.SUCCESS.value,
            "chunks": [chunk.model_dump(mode="json") for chunk in chunks],
        }
    )


@tool
def get_calculator_link(
    state: Annotated[State, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Ссылка на официальный калькулятор стоимости окон Melke.

    Стоимость в диалоге не рассчитывается: калькулятор даёт предварительный расчёт,
    точная сумма подтверждается после замера.
    """
    url = get_settings().calculator_url
    return Command(
        update={
            "calculator_url": url,
            "messages": [
                ToolMessage(
                    content=_json({"status": ToolStatus.SUCCESS.value, "calculator_url": url}),
                    tool_call_id=tool_call_id,
                )
            ],
        }
    )


@tool
def get_manager(
    reason: str,
    state: Annotated[State, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """Передача обращения менеджеру Melke.

    MVP: обращение фиксируется в контексте диалога и логе, отправка менеджеру не выполняется.
    reason — причина передачи своими словами на основании слов клиента.
    """
    user = state["user"]
    handoff = state["manager_handoff"].model_copy(
        update={
            "status": ManagerHandoffStatus.PENDING,
            "additional_comment": reason,
        }
    )
    logger.info(
        "Обращение клиента id=%s передано менеджеру (заглушка): %s",
        user.id_user,
        reason,
    )
    return Command(
        update={
            "manager_handoff": handoff,
            "messages": [
                ToolMessage(
                    content=_json(
                        {
                            "status": ToolStatus.SUCCESS.value,
                            "handoff_status": ManagerHandoffStatus.PENDING.value,
                        }
                    ),
                    tool_call_id=tool_call_id,
                )
            ],
        }
    )


@tool
def save_customer_profile(
    state: Annotated[State, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
    profile_color: str | None = None,
    lamination_color: str | None = None,
    lamination_type: str | None = None,
    mass_coloration_of_the_profile: bool | None = None,
    window_shape: str | None = None,
    number_of_leaves: int | None = None,
    opening_type: str | None = None,
    insulated_glass_unit: str | None = None,
    decorative_grilles: bool | None = None,
    windowsills: str | None = None,
    mosquito_nets: bool | None = None,
    child_safety: bool | None = None,
    window_sills_and_reveals: bool | None = None,
    hardware: str | None = None,
    budget_amount: int | None = None,
    budget_level: Literal["low", "medium", "high"] | None = None,
    currency: str | None = None,
    legal_entity: bool | None = None,
    clients_building_type: str | None = None,
    product_category: str | None = None,
    contact_name: str | None = None,
    contact_phone: str | None = None,
    contact_email: str | None = None,
) -> Command:
    """Сохраняет в контекст диалога сведения, которые клиент сообщил о себе.

    Передавай только то, что клиент действительно сказал; неизвестное оставляй пустым.
    Уже известные сведения не затираются.
    """
    user = state["user"]

    preferences_updates = {
        "profile_color": profile_color,
        "lamination_color": lamination_color,
        "lamination_type": lamination_type,
        "mass_coloration_of_the_profile": mass_coloration_of_the_profile,
        "window_shape": window_shape,
        "number_of_leaves": number_of_leaves,
        "opening_type": opening_type,
        "insulated_glass_unit": insulated_glass_unit,
        "decorative_grilles": decorative_grilles,
        "windowsills": windowsills,
        "mosquito_nets": mosquito_nets,
        "child_safety": child_safety,
        "window_sills_and_reveals": window_sills_and_reveals,
        "hardware": hardware,
    }
    context_updates = {
        "budget_amount": budget_amount,
        "budget_level": BudgetLevel(budget_level) if budget_level else None,
        "currency": currency,
        "legal_entity": legal_entity,
        "clients_building_type": clients_building_type,
        "product_category": product_category,
    }
    contact_updates = {
        "name": contact_name,
        "phone": contact_phone,
        "email": contact_email,
    }

    preferences = user.context_user.preferences.model_copy(
        update=_provided(preferences_updates)
    )
    context = user.context_user.model_copy(
        update={**_provided(context_updates), "preferences": preferences}
    )
    contact = user.contact_info.model_copy(update=_provided(contact_updates))
    updated_user = user.model_copy(
        update={"context_user": context, "contact_info": contact}
    )

    saved = sorted(
        key
        for key, value in {**preferences_updates, **context_updates, **contact_updates}.items()
        if value is not None
    )
    logger.info("Сохранены сведения о клиенте id=%s: %s", user.id_user, saved or "нет новых")
    return Command(
        update={
            "user": updated_user,
            "messages": [
                ToolMessage(
                    content=_json(
                        {
                            "status": ToolStatus.SUCCESS.value,
                            "saved": saved,
                        }
                    ),
                    tool_call_id=tool_call_id,
                )
            ],
        }
    )


def _provided(updates: dict[str, Any]) -> dict[str, Any]:
    """Оставляет только действительно сообщённые клиентом значения."""
    return {key: value for key, value in updates.items() if value is not None}


TOOLS = [
    search_products,
    search_knowledge,
    get_calculator_link,
    get_manager,
    save_customer_profile,
]

__all__ = ["TOOLS"]
