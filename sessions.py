"""Сессии диалога: один экземпляр Agent на session_id, состояние только в памяти процесса."""

import asyncio
from uuid import uuid4

from agent import Agent


class SessionNotFound(Exception):
    """Сессия не найдена: идентификатор неизвестен или сессия удалена."""


class SessionStore:
    """Хранилище сессий в памяти процесса.

    Контекст диалога живёт внутри `Agent`, поэтому сессия — это отдельный экземпляр агента.
    После перезапуска приложения сессии теряются (решение для MVP).
    """

    def __init__(self) -> None:
        self._agents: dict[str, Agent] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def create(self) -> str:
        """Создаёт сессию и возвращает непредсказуемый идентификатор."""
        session_id = str(uuid4())
        self._agents[session_id] = Agent()
        self._locks[session_id] = asyncio.Lock()
        return session_id

    def get(self, session_id: str) -> Agent:
        try:
            return self._agents[session_id]
        except KeyError:
            raise SessionNotFound(session_id) from None

    def lock(self, session_id: str) -> asyncio.Lock:
        """Блокировка сессии: сообщения одного диалога обрабатываются по очереди."""
        self.get(session_id)
        return self._locks[session_id]

    def delete(self, session_id: str) -> None:
        self.get(session_id)
        self._agents.pop(session_id, None)
        self._locks.pop(session_id, None)
