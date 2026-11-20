"""Конфигурация агента: DeepSeek, калькулятор, лимиты."""

from functools import lru_cache

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
