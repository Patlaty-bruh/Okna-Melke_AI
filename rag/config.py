"""Конфигурация модуля векторной базы знаний RAG."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Документы кодируются без инструкции, запросы — с инструкцией (так рекомендует карточка модели).
# Инструкция на английском: модель обучалась на англоязычных инструкциях.
DEFAULT_QUERY_INSTRUCTION = (
    "Instruct: Given a customer question about plastic windows, retrieve passages "
    "from the manufacturer's documentation that answer the question.\nQuery: "
)

# Категория документа определяется по имени исходного файла базы знаний.
CATEGORY_BY_FILE: dict[str, str] = {
    "company.md": "company",
    "production_and_technology.md": "technology",
    "products_overview.md": "products",
    "installation.md": "installation",
    "warranty.md": "warranty",
    "maintenance.md": "maintenance",
    "ordering.md": "ordering",
    "delivery.md": "delivery",
    "faq.md": "faq",
    "official_documents.md": "documents",
}

DEFAULT_CATEGORY = "other"

# Названия продуктов Melke, которые встречаются в заголовках разделов документов.
PRODUCT_NAMES: tuple[str, ...] = (
    "Melke Centum",
    "Melke Wide",
    "Melke Evolution",
    "Melke Smart Ultra",
    "Melke Lite 70",
    "Melke Lite 60",
    "Cool Colours",
    "Melke Elastic",
    "Alurix",
)


class Settings(BaseSettings):
    """Параметры индексации, модели эмбеддингов и подключения к PostgreSQL."""

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # PostgreSQL + pgvector
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "melke"
    postgres_user: str = "melke"
    postgres_password: str = ""

    # Исходные документы
    knowledge_base_dir: Path = PROJECT_ROOT / "knowledge_base"
    excluded_files: list[str] = ["README.md"]

    # Эмбеддинги
    embedding_model: str = "Qwen/Qwen3-Embedding-0.6B"
    embedding_dim: int = 1024
    embedding_device: str | None = None
    embedding_dtype: str = "float32"
    embedding_batch_size: int = 8
    query_instruction: str = DEFAULT_QUERY_INSTRUCTION

    # Чанки
    chunk_size: int = 2000
    chunk_overlap: int = 300

    # Поиск
    search_limit: int = 6
    dense_candidates: int = 20
    sparse_candidates: int = 20
    rrf_k: int = 60

    @property
    def conninfo_kwargs(self) -> dict[str, object]:
        return {
            "host": self.postgres_host,
            "port": self.postgres_port,
            "dbname": self.postgres_db,
            "user": self.postgres_user,
            "password": self.postgres_password,
        }

    def category_for(self, source_file: str) -> str:
        return CATEGORY_BY_FILE.get(Path(source_file).name, DEFAULT_CATEGORY)
