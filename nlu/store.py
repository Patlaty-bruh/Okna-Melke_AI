"""Векторы вопросов FAQ в PostgreSQL (pgvector)."""

from typing import Any

import psycopg
from pgvector.psycopg import register_vector
from pydantic import BaseModel

from rag.config import Settings

HNSW_MIN_VERSION = (0, 5, 0)


class FaqQuestion(BaseModel):
    """Одна формулировка вопроса FAQ вместе с её вектором."""

    entry_id: str
    category: str
    text: str
    question: str
    answer: str
    source_url: str | None
    embedding: list[float]


class FaqMatch(BaseModel):
    """Найденный вопрос FAQ."""

    entry_id: str
    category: str
    question: str
    answer: str
    source_url: str | None
    matched_text: str
    score: float


class FaqStore:
    """Таблица faq_entries: строка — формулировка вопроса (сам вопрос или его вариант).

    Ответ хранится рядом с формулировкой, поэтому поиск отдаёт готовый ответ
    без дополнительных запросов.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._conn = psycopg.connect(**settings.conninfo_kwargs, autocommit=True)
        self._conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        register_vector(self._conn)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "FaqStore":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # ---------- схема ----------

    def ensure_schema(self) -> None:
        dimension = self._settings.embedding_dim
        with self._conn.transaction():
            self._conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS faq_entries (
                    id          BIGSERIAL PRIMARY KEY,
                    entry_id    TEXT NOT NULL,
                    category    TEXT NOT NULL,
                    text        TEXT NOT NULL,
                    question    TEXT NOT NULL,
                    answer      TEXT NOT NULL,
                    source_url  TEXT,
                    embedding   VECTOR({dimension}) NOT NULL,
                    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
                    UNIQUE (entry_id, text)
                )
                """
            )
            version = self._conn.execute(
                "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
            ).fetchone()
            if version is None:
                raise RuntimeError("Расширение pgvector не установлено")
            version_tuple = tuple(int(part) for part in version[0].split(".")[:3])
            if version_tuple >= HNSW_MIN_VERSION:
                self._conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS faq_entries_embedding_hnsw_idx
                    ON faq_entries USING hnsw (embedding vector_cosine_ops)
                    """
                )
            else:
                self._conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS faq_entries_embedding_ivfflat_idx
                    ON faq_entries USING ivfflat (embedding vector_cosine_ops)
                    WITH (lists = 100)
                    """
                )

    # ---------- чтение ----------

    def formulation_count(self) -> int:
        return self._conn.execute("SELECT count(*) FROM faq_entries").fetchone()[0]

    def entry_count(self) -> int:
        return self._conn.execute(
            "SELECT count(DISTINCT entry_id) FROM faq_entries"
        ).fetchone()[0]

    # ---------- запись ----------

    def replace_all(self, questions: list[FaqQuestion]) -> int:
        """Полностью заменяет содержимое таблицы (датасет небольшой)."""

        rows = [
            (
                item.entry_id,
                item.category,
                item.text,
                item.question,
                item.answer,
                item.source_url,
                item.embedding,
            )
            for item in questions
        ]
        with self._conn.transaction():
            self._conn.execute("DELETE FROM faq_entries")
            with self._conn.cursor() as cursor:
                cursor.executemany(
                    """
                    INSERT INTO faq_entries (
                        entry_id, category, text, question, answer, source_url, embedding
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s::vector)
                    """,
                    rows,
                )
        return len(rows)

    # ---------- поиск ----------

    def search(
        self,
        embedding: list[float],
        threshold: float,
        limit: int = 3,
    ) -> list[FaqMatch]:
        """Ищет вопросы FAQ, похожие на сообщение клиента."""

        params: dict[str, Any] = {
            "vector": embedding,
            "threshold": threshold,
            "limit": limit,
        }
        rows = self._conn.execute(
            """
            SELECT entry_id, category, question, answer, source_url, text,
                   1 - (embedding <=> %(vector)s::vector) AS score
            FROM faq_entries
            WHERE 1 - (embedding <=> %(vector)s::vector) >= %(threshold)s
            ORDER BY embedding <=> %(vector)s::vector
            LIMIT %(limit)s
            """,
            params,
        ).fetchall()
        return [
            FaqMatch(
                entry_id=row[0],
                category=row[1],
                question=row[2],
                answer=row[3],
                source_url=row[4],
                matched_text=row[5],
                score=float(row[6]),
            )
            for row in rows
        ]
