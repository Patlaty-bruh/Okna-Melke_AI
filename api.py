"""FastAPI-интерфейс агента Melke: сессии, диалог, стриминг ответа, проверка доступности."""

import asyncio
import json
import logging
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

from agent import Agent, flush_tracing
from config import get_api_settings
from models import AgentResponse
from nlu.index import ensure_indexed
from nlu.layer import get_layer
from sessions import SessionNotFound, SessionStore

logger = logging.getLogger(__name__)

# В контейнере своего logging-конфига нет: включаем логи приложения с уровнем из LOG_LEVEL.
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(levelname)s %(name)s: %(message)s",
)
# Библиотеки шумят каждым HTTP-запросом к DeepSeek и HuggingFace.
for noisy in ("httpx", "httpx2", "httpcore", "httpcore2"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

FRONTEND_DIR = Path(__file__).resolve().parent / "frontend"

# Ограничения входных данных (§4.1, §4.6)
SESSION_ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"
MESSAGE_MAX_LENGTH = 2000
MAX_REQUEST_BYTES = 16 * 1024

# HTTP-статус -> код ошибки для frontend
ERROR_CODES: dict[int, str] = {
    status.HTTP_400_BAD_REQUEST: "invalid_request",
    status.HTTP_404_NOT_FOUND: "session_not_found",
    status.HTTP_413_CONTENT_TOO_LARGE: "request_too_large",
    status.HTTP_422_UNPROCESSABLE_CONTENT: "invalid_request",
    status.HTTP_500_INTERNAL_SERVER_ERROR: "internal_error",
    status.HTTP_502_BAD_GATEWAY: "agent_error",
    status.HTTP_503_SERVICE_UNAVAILABLE: "service_unavailable",
    status.HTTP_504_GATEWAY_TIMEOUT: "agent_timeout",
}
DEFAULT_ERROR_CODE = "internal_error"

MESSAGES = {
    "invalid_request": "Некорректный запрос: проверьте обязательные поля и их формат.",
    "session_not_found": "Сессия не найдена или уже удалена.",
    "request_too_large": "Запрос слишком большой.",
    "agent_error": "Не удалось получить ответ агента. Попробуйте позже.",
    "agent_timeout": "Превышено время ожидания ответа агента. Попробуйте позже.",
    "service_unavailable": "Сервис агента временно недоступен.",
    "internal_error": "Внутренняя ошибка сервиса.",
}


# ---------- Схемы API ----------


class SessionResponse(BaseModel):
    session_id: str


class ChatRequest(BaseModel):
    session_id: str = Field(pattern=SESSION_ID_PATTERN)
    message: str = Field(min_length=1, max_length=MESSAGE_MAX_LENGTH)

    @field_validator("message")
    @classmethod
    def _message_not_blank(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("Сообщение не должно быть пустым")
        return text


class HealthResponse(BaseModel):
    status: str = "ok"


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody


# ---------- Приложение ----------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Восстанавливает индекс FAQ в фоне: после пересоздания БД ручных шагов не нужно.

    Индексация не задерживает старт и `/health`: она идёт параллельно с запросами,
    а до ее завершения NLU-слой просто передаёт сообщения агенту.
    """
    indexing = asyncio.create_task(_prepare_faq_index())
    yield
    indexing.cancel()
    with suppress(asyncio.CancelledError):
        await indexing
    # Opik отправляет трейсы батчами: при остановке досылаем оставшиеся.
    await asyncio.to_thread(flush_tracing)


async def _prepare_faq_index() -> None:
    settings = get_layer().settings
    if not settings.faq_enabled:
        return
    try:
        indexed = await asyncio.to_thread(ensure_indexed, settings)
        logger.info("Индекс FAQ готов (пересчитан: %s)", "да" if indexed else "нет")
    except Exception:
        logger.exception("Не удалось подготовить индекс FAQ: слой уйдёт в агент")


app = FastAPI(title="Melke Agent API", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_api_settings().cors_origin_list,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type"],
    allow_credentials=False,
)

store = SessionStore()


@app.middleware("http")
async def limit_request_size(request: Request, call_next):
    """Отсекает слишком большие запросы до чтения тела."""
    length = request.headers.get("content-length", "")
    if length.isdigit() and int(length) > MAX_REQUEST_BYTES:
        return _error_response(status.HTTP_413_CONTENT_TOO_LARGE)
    return await call_next(request)


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Проверка доступности Backend: без обращения к БД и LLM."""
    return HealthResponse()


@app.post(
    "/sessions",
    response_model=SessionResponse,
    status_code=status.HTTP_201_CREATED,
    responses={503: {"model": ErrorResponse}},
)
def create_session() -> SessionResponse:
    """Создаёт сессию для диалога с агентом."""
    try:
        session_id = store.create()
    except RuntimeError:
        logger.exception("Не удалось создать сессию: агент не сконфигурирован")
        raise _error(status.HTTP_503_SERVICE_UNAVAILABLE) from None
    return SessionResponse(session_id=session_id)


@app.delete(
    "/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={404: {"model": ErrorResponse}},
)
def delete_session(session_id: str) -> Response:
    """Удаляет сессию и её контекст диалога."""
    try:
        store.delete(session_id)
    except SessionNotFound:
        raise _error(status.HTTP_404_NOT_FOUND) from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.post(
    "/chat",
    response_model=AgentResponse,
    responses={
        404: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
        504: {"model": ErrorResponse},
    },
)
async def chat(payload: ChatRequest) -> AgentResponse:
    """Отправляет сообщение агенту и возвращает полный структурированный ответ."""
    agent = _session_agent(payload.session_id)
    async with store.lock(payload.session_id):
        return _validated(await _ask(agent, payload.message))


@app.post(
    "/chat/stream",
    responses={
        200: {"content": {"text/event-stream": {}}},
        404: {"model": ErrorResponse},
    },
)
async def chat_stream(payload: ChatRequest) -> StreamingResponse:
    """Отправляет сообщение агенту и стримит ответ событиями SSE.

    События: `delta` — часть текста ответа, `done` — полный структурированный ответ,
    `error` — сообщение об ошибке без технических подробностей.
    """
    agent = _session_agent(payload.session_id)
    return StreamingResponse(
        _stream(agent, payload.session_id, payload.message),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------- Взаимодействие с агентом ----------


def _session_agent(session_id: str) -> Agent:
    try:
        return store.get(session_id)
    except SessionNotFound:
        raise _error(status.HTTP_404_NOT_FOUND) from None


async def _ask(agent: Agent, message: str) -> AgentResponse:
    timeout = get_api_settings().agent_turn_timeout
    started = time.perf_counter()
    try:
        async with asyncio.timeout(timeout):
            response = await _prepared(agent, message)
            if response is None:
                response = await agent.ask(message)
    except TimeoutError:
        logger.warning("Превышено время ожидания ответа агента: %.0f c", timeout)
        raise _error(status.HTTP_504_GATEWAY_TIMEOUT) from None
    except HTTPException:
        raise
    except Exception:
        logger.exception("Ошибка выполнения агента")
        raise _error(status.HTTP_502_BAD_GATEWAY) from None
    logger.info("Ответ получен за %.1f c", time.perf_counter() - started)
    return response


async def _prepared(agent: Agent, message: str) -> AgentResponse | None:
    """Заготовленный ответ NLU-слоя или None, если отвечать должен агент."""
    try:
        answer = await asyncio.to_thread(get_layer().reply, message)
    except Exception:
        logger.exception("NLU-слой недоступен, сообщение обрабатывает агент")
        return None
    if answer is None:
        return None
    return agent.record_ready_answer(message, answer)


def _validated(response: AgentResponse) -> AgentResponse:
    """Проверяет, что агент вернул данные, соответствующие контракту API."""
    try:
        return AgentResponse.model_validate(response.model_dump())
    except ValidationError:
        logger.exception("Ответ агента не соответствует контракту")
        raise _error(status.HTTP_502_BAD_GATEWAY) from None


async def _stream(agent: Agent, session_id: str, message: str) -> AsyncIterator[str]:
    timeout = get_api_settings().agent_turn_timeout
    started = time.perf_counter()
    try:
        async with store.lock(session_id), asyncio.timeout(timeout):
            prepared = await _prepared(agent, message)
            if prepared is not None:
                yield _sse("delta", {"text": prepared.message})
                yield _sse("done", _validated(prepared).model_dump(mode="json"))
                return
            async for item in agent.ask_stream(message):
                if isinstance(item, AgentResponse):
                    yield _sse("done", _validated(item).model_dump(mode="json"))
                elif item:
                    yield _sse("delta", {"text": item})
    except TimeoutError:
        logger.warning("Превышено время ожидания ответа агента: %.0f c", timeout)
        yield _sse("error", {"message": MESSAGES["agent_timeout"]})
    except Exception:
        logger.exception("Ошибка стриминга ответа агента")
        yield _sse("error", {"message": MESSAGES["agent_error"]})
    else:
        logger.info("Поток ответа завершён за %.1f c", time.perf_counter() - started)


# ---------- Обработка ошибок ----------


def _error(status_code: int) -> HTTPException:
    code = ERROR_CODES.get(status_code, DEFAULT_ERROR_CODE)
    return HTTPException(status_code=status_code, detail=MESSAGES[code])


def _error_response(status_code: int) -> JSONResponse:
    body = ErrorResponse(error=ErrorBody(**_error_payload(status_code)))
    return JSONResponse(status_code=status_code, content=body.model_dump())


def _error_payload(status_code: int) -> dict[str, str]:
    code = ERROR_CODES.get(status_code, DEFAULT_ERROR_CODE)
    return {"code": code, "message": MESSAGES[code]}


def _sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(request: Request, error: StarletteHTTPException) -> JSONResponse:
    return _error_response(error.status_code)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(
    request: Request, error: RequestValidationError
) -> JSONResponse:
    # В лог попадают только имена полей: значения запроса (в том числе session_id) не пишем.
    logger.info("Некорректный запрос: %s", [item.get("loc") for item in error.errors()])
    return _error_response(status.HTTP_422_UNPROCESSABLE_CONTENT)


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, error: Exception) -> JSONResponse:
    logger.exception("Необработанная ошибка запроса")
    return _error_response(status.HTTP_500_INTERNAL_SERVER_ERROR)


# ---------- Статика frontend ----------

if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
