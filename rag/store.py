"""Хранение чанков в PostgreSQL: векторы (pgvector) и лексический индекс (BM25)."""

import json
from typing import Any

import psycopg
from pgvector.psycopg import register_vector
from pydantic import BaseModel

from rag.chunking import Chunk
from rag.config import Settings
from rag.documents import KnowledgeDocument

HNSW_MIN_VERSION = (0, 5, 0)

# Классические параметры BM25.
BM25_K1 = 1.2
BM25_B = 0.75

# Словарь PostgreSQL для лексического поиска (стемминг русского языка).
TEXT_SEARCH_CONFIG = "russian"


class EmbeddedChunk(BaseModel):
    """Чанк вместе с его вектором."""

    chunk: Chunk
    embedding: list[float]


class SearchHit(BaseModel):
    """Результат поиска (dense или sparse)."""

    id: int
    source_file: str
    section: str | None
    source_url: str | None
    category: str | None
    product_name: str | None
    score: float
    text: str


class VectorStore:
    """Хранилище чанков базы знаний.

    Таблицы:
      knowledge_documents   — реестр документов с хешем содержимого;
      knowledge_chunks      — чанки, их векторы и метаданные;
      knowledge_chunk_terms — обратный индекс лексем для BM25.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        # autocommit=True: явные блоки transaction() становятся реальными транзакциями,
        # а не точками сохранения внутри незавершённой неявной транзакции.
        self._conn = psycopg.connect(**settings.conninfo_kwargs, autocommit=True)
        self._conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        register_vector(self._conn)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "VectorStore":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # ---------- схема ----------

    def ensure_schema(self) -> None:
        dimension = self._settings.embedding_dim
        with self._conn.transaction():
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS knowledge_documents (
                    source_file   TEXT PRIMARY KEY,
                    content_hash  TEXT NOT NULL,
                    chunk_count   INTEGER NOT NULL,
                    indexed_at    TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            self._conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS knowledge_chunks (
                    id            BIGSERIAL PRIMARY KEY,
                    source_file   TEXT NOT NULL
                                  REFERENCES knowledge_documents (source_file)
                                  ON DELETE CASCADE,
                    chunk_index   INTEGER NOT NULL,
                    text          TEXT NOT NULL,
                    embedding     VECTOR({dimension}) NOT NULL,
                    source_url    TEXT,
                    category      TEXT,
                    product_name  TEXT,
                    section       TEXT,
                    page_number   INTEGER,
                    token_count   INTEGER,
                    metadata      JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
                    UNIQUE (source_file, chunk_index)
                )
                """
            )
            # Колонка добавлена после первой версии схемы.
            self._conn.execute(
                "ALTER TABLE knowledge_chunks ADD COLUMN IF NOT EXISTS token_count INTEGER"
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS knowledge_chunk_terms (
                    chunk_id  BIGINT NOT NULL
                              REFERENCES knowledge_chunks (id) ON DELETE CASCADE,
                    lexeme    TEXT NOT NULL,
                    tf        INTEGER NOT NULL,
                    PRIMARY KEY (chunk_id, lexeme)
                )
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS knowledge_chunk_terms_lexeme_idx
                ON knowledge_chunk_terms (lexeme)
                """
            )
            self._create_vector_index()
            self._backfill_lexical_index()

    def _create_vector_index(self) -> None:
        version = self._conn.execute(
            "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
        ).fetchone()
        if version is None:
            raise RuntimeError("Расширение pgvector не установлено")
        version_tuple = tuple(int(part) for part in version[0].split(".")[:3])

        # HNSW появился в pgvector 0.5.0; для более старых версий используем IVFFlat.
        if version_tuple >= HNSW_MIN_VERSION:
            statement = """
                CREATE INDEX IF NOT EXISTS knowledge_chunks_embedding_hnsw_idx
                ON knowledge_chunks USING hnsw (embedding vector_cosine_ops)
            """
        else:
            statement = """
                CREATE INDEX IF NOT EXISTS knowledge_chunks_embedding_ivfflat_idx
                ON knowledge_chunks USING ivfflat (embedding vector_cosine_ops)
                WITH (lists = 100)
            """
        self._conn.execute(statement)

    # ---------- лексический индекс (BM25) ----------

    def _populate_terms(self, source_file: str | None = None) -> None:
        """Заполняет обратный индекс лексем для документа или для всех чанков без индекса."""

        if source_file is None:
            scope = """NOT EXISTS (
                SELECT 1 FROM knowledge_chunk_terms ct WHERE ct.chunk_id = c.id
            )"""
            params: tuple[Any, ...] = (TEXT_SEARCH_CONFIG,)
        else:
            scope = "c.source_file = %s"
            params = (TEXT_SEARCH_CONFIG, source_file)

        self._conn.execute(
            f"""
            INSERT INTO knowledge_chunk_terms (chunk_id, lexeme, tf)
            SELECT c.id, terms.lexeme, terms.tf
            FROM knowledge_chunks c
            CROSS JOIN LATERAL (
                SELECT lexeme, sum(array_length(positions, 1))::integer AS tf
                FROM unnest(to_tsvector(%s::regconfig, c.text))
                GROUP BY lexeme
            ) AS terms
            WHERE {scope}
            ON CONFLICT (chunk_id, lexeme) DO NOTHING
            """,
            params,
        )

    def _refresh_token_counts(self, source_file: str | None = None) -> None:
        """Пересчитывает длину чанка в лексемах (dl для BM25)."""

        scope = "TRUE" if source_file is None else "c.source_file = %s"
        params: tuple[Any, ...] = () if source_file is None else (source_file,)
        self._conn.execute(
            f"""
            UPDATE knowledge_chunks c
            SET token_count = COALESCE(
                (SELECT sum(ct.tf) FROM knowledge_chunk_terms ct WHERE ct.chunk_id = c.id),
                0
            )
            WHERE {scope}
            """,
            params,
        )

    def _backfill_lexical_index(self) -> None:
        """Достраивает лексический индекс для чанков, попавших в БД до его появления."""

        missing = self._conn.execute(
            "SELECT count(*) FROM knowledge_chunks WHERE token_count IS NULL"
        ).fetchone()[0]
        if not missing:
            return
        self._populate_terms()
        self._refresh_token_counts()

    def embedding_column_type(self) -> str:
        row = self._conn.execute(
            """
            SELECT format_type(atttypid, atttypmod)
            FROM pg_attribute
            WHERE attrelid = 'knowledge_chunks'::regclass AND attname = 'embedding'
            """
        ).fetchone()
        if row is None:
            raise RuntimeError("Столбец knowledge_chunks.embedding не найден")
        return row[0]

    # ---------- чтение ----------

    def document_hash(self, source_file: str) -> str | None:
        row = self._conn.execute(
            "SELECT content_hash FROM knowledge_documents WHERE source_file = %s",
            (source_file,),
        ).fetchone()
        return row[0] if row else None

    def chunk_count(self) -> int:
        return self._conn.execute("SELECT count(*) FROM knowledge_chunks").fetchone()[0]

    # ---------- запись ----------

    def replace_document(
        self,
        document: KnowledgeDocument,
        chunks: list[EmbeddedChunk],
    ) -> int:
        """Заменяет все чанки документа новыми в одной транзакции."""

        rows = [
            (
                document.source_file,
                item.chunk.index,
                item.chunk.text,
                item.embedding,
                item.chunk.source_url,
                document.category,
                item.chunk.product_name,
                item.chunk.section,
                None,  # page_number: для Markdown-документов неприменимо
                _to_json(item.chunk.metadata),
            )
            for item in chunks
        ]

        with self._conn.transaction():
            self._conn.execute(
                """
                INSERT INTO knowledge_documents (source_file, content_hash, chunk_count)
                VALUES (%s, %s, %s)
                ON CONFLICT (source_file) DO UPDATE
                SET content_hash = EXCLUDED.content_hash,
                    chunk_count = EXCLUDED.chunk_count,
                    indexed_at = now()
                """,
                (document.source_file, document.content_hash, len(rows)),
            )
            self._conn.execute(
                "DELETE FROM knowledge_chunks WHERE source_file = %s",
                (document.source_file,),
            )
            with self._conn.cursor() as cursor:
                cursor.executemany(
                    """
                    INSERT INTO knowledge_chunks (
                        source_file, chunk_index, text, embedding, source_url,
                        category, product_name, section, page_number, metadata
                    ) VALUES (%s, %s, %s, %s::vector, %s, %s, %s, %s, %s, %s)
                    """,
                    rows,
                )
            self._populate_terms(document.source_file)
            self._refresh_token_counts(document.source_file)
        return len(rows)

    # ---------- поиск ----------

    def dense_search(
        self,
        embedding: list[float],
        limit: int = 5,
        categories: list[str] | None = None,
    ) -> list[SearchHit]:
        params: dict[str, Any] = {"vector": embedding, "limit": limit}
        where = ""
        if categories:
            where = "WHERE category = ANY(%(categories)s)"
            params["categories"] = categories

        sql = f"""
            SELECT id, source_file, section, source_url, category, product_name,
                   text, 1 - (embedding <=> %(vector)s::vector) AS score
            FROM knowledge_chunks
            {where}
            ORDER BY embedding <=> %(vector)s::vector
            LIMIT %(limit)s
        """
        rows = self._conn.execute(sql, params).fetchall()
        return [
            SearchHit(
                id=row[0],
                source_file=row[1],
                section=row[2],
                source_url=row[3],
                category=row[4],
                product_name=row[5],
                text=row[6],
                score=float(row[7]),
            )
            for row in rows
        ]

    def sparse_search(
        self,
        query: str,
        limit: int = 20,
        categories: list[str] | None = None,
    ) -> list[SearchHit]:
        """Лексический поиск BM25 по обратному индексу лексем."""

        where = "WHERE c.category = ANY(%(categories)s)" if categories else ""
        sql = f"""
            WITH query_terms AS (
                SELECT array_agg(DISTINCT lexeme) AS lexemes
                FROM unnest(to_tsvector(%(config)s::regconfig, %(query)s))
            ),
            corpus AS (
                SELECT count(*)::float8 AS chunks,
                       GREATEST(avg(COALESCE(token_count, 0)), 1)::float8 AS avg_tokens
                FROM knowledge_chunks
            ),
            term_df AS (
                SELECT t.lexeme, count(*)::float8 AS df
                FROM knowledge_chunk_terms t, query_terms q
                WHERE t.lexeme = ANY (q.lexemes)
                GROUP BY t.lexeme
            ),
            scored AS (
                SELECT ct.chunk_id,
                       sum(
                           ln(1 + (corpus.chunks - term_df.df + 0.5) / (term_df.df + 0.5))
                           * (ct.tf * (%(k1)s + 1))
                           / (ct.tf + %(k1)s * (1 - %(b)s + %(b)s * COALESCE(c.token_count, 0)::float8 / corpus.avg_tokens))
                       ) AS score
                FROM knowledge_chunk_terms ct
                JOIN term_df ON term_df.lexeme = ct.lexeme
                JOIN knowledge_chunks c ON c.id = ct.chunk_id
                CROSS JOIN corpus
                {where}
                GROUP BY ct.chunk_id
            )
            SELECT c.id, c.source_file, c.section, c.source_url, c.category,
                   c.product_name, c.text, s.score
            FROM scored s
            JOIN knowledge_chunks c ON c.id = s.chunk_id
            ORDER BY s.score DESC
            LIMIT %(limit)s
        """
        params: dict[str, Any] = {
            "query": query,
            "config": TEXT_SEARCH_CONFIG,
            "k1": BM25_K1,
            "b": BM25_B,
            "limit": limit,
        }
        if categories:
            params["categories"] = categories

        rows = self._conn.execute(sql, params).fetchall()
        return [
            SearchHit(
                id=row[0],
                source_file=row[1],
                section=row[2],
                source_url=row[3],
                category=row[4],
                product_name=row[5],
                text=row[6],
                score=float(row[7]),
            )
            for row in rows
        ]


def _to_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False)
