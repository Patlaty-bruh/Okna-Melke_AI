"""Конфигурация агента: DeepSeek, калькулятор, лимиты."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

from rag.config import PROJECT_ROOT
from rag.config import Settings as RagSettings


class Settings(RagSettings):
    """Настройки агента. Наследует подключение к PostgreSQL и параметры RAG."""

    # DeepSeek (OpenAI-совместимый API)
    deepseek_api: str = ""
    deepseek_model: str = "deepseek-flash"
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_temperature: float = 0.3
    deepseek_timeout: float = 60.0

    calculator_url: str = "https://www.melke.ru/wincalc/"

    # Opik: трейсинг ходов агента (LLM-вызовы, инструменты, токены)
    opik_api_key: str = ""
    opik_project_name: str = "melke-agent"

    @property
    def opik_enabled(self) -> bool:
        return bool(self.opik_api_key)

    # Контекст диалога: последние 25 сообщений
    history_messages_limit: int = 25
    # Границы числа результатов поиска
    catalog_search_max_limit: int = 10
    knowledge_search_min_limit: int = 3
    knowledge_search_max_limit: int = 8


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    if not settings.deepseek_api:
        raise RuntimeError("Не задан DEEPSEEK_API в .env")
    return settings


class ApiSettings(BaseSettings):
    """Настройки FastAPI-интерфейса.

    Не требуют ключа DeepSeek: проверка доступности и создание сессии должны
    работать независимо от внешних сервисов.
    """

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Список адресов frontend через запятую
    cors_origins: str = "http://localhost:8080,http://127.0.0.1:8080"
    # Предельное время обработки одного сообщения агентом, секунды.
    # Запас нужен на первый запрос: модель эмбеддингов RAG (~1.2 ГБ) загружается
    # внутри хода агента и на холодном кэше занимает ~5 минут.
    agent_turn_timeout: float = 600.0

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache(maxsize=1)
def get_api_settings() -> ApiSettings:
    return ApiSettings()
