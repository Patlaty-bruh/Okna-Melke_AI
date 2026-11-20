"""CLI модуля векторной базы знаний: индексация и проверка Dense Search."""

import argparse
import sys

from rag.chunking import split_document
from rag.config import Settings
from rag.documents import load_documents
from rag.embeddings import Embedder
from rag.search import HybridSearcher
from rag.store import EmbeddedChunk, VectorStore


def _configure_stdout() -> None:
    # Windows-консоль по умолчанию не всегда в UTF-8.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")


def index_knowledge_base(settings: Settings) -> int:
    """Синхронизирует базу знаний с векторным хранилищем. Возвращает число ошибок."""

    documents, errors_messages = load_documents(settings)
    print(f"Найдено документов: {len(documents)}")
    for message in errors_messages:
        print(f"[error] {message}", file=sys.stderr)

    with VectorStore(settings) as store:
        store.ensure_schema()
        column_type = store.embedding_column_type()
        expected_type = f"vector({settings.embedding_dim})"
        if column_type != expected_type:
            raise RuntimeError(
                f"Тип столбца embedding ({column_type}) не совпадает "
                f"с размерностью модели ({expected_type})"
            )

        changed = [
            document
            for document in documents
            if store.document_hash(document.source_file) != document.content_hash
        ]
        print(f"Требуют индексации: {len(changed)}")

        if not changed:
            print(f"Все документы актуальны, чанков в БД: {store.chunk_count()}")
            return 1 if errors_messages else 0

        embedder = Embedder(settings)
        if embedder.dimension != settings.embedding_dim:
            raise RuntimeError(
                f"Модель {embedder.model_name} отдаёт векторы размерности "
                f"{embedder.dimension}, а в конфигурации указано {settings.embedding_dim}"
            )

        errors = 0
        total_chunks = 0
        for document in changed:
            try:
                chunks = split_document(document, settings)
                vectors = embedder.embed_documents([chunk.text for chunk in chunks])
                embedded = [
                    EmbeddedChunk(chunk=chunk, embedding=vector)
                    for chunk, vector in zip(chunks, vectors, strict=True)
                ]
                total_chunks += store.replace_document(document, embedded)
                print(f"[ok] {document.source_file}: чанков {len(embedded)}")
            except Exception as error:
                errors += 1
                print(
                    f"[error] {document.source_file}: {type(error).__name__}: {error}",
                    file=sys.stderr,
                )

        print(
            f"Обновлено документов: {len(changed) - errors}, "
            f"чанков записано: {total_chunks}, ошибок: {errors + len(errors_messages)}, "
            f"всего чанков в БД: {store.chunk_count()}"
        )
    return 1 if errors or errors_messages else 0


def search_knowledge_base(
    settings: Settings,
    query: str,
    limit: int,
    category: str | None,
    mode: str,
) -> int:
    """Проверочный поиск: hybrid (RRF), dense или sparse."""

    if mode == "sparse":
        categories = [category] if category else None
        with VectorStore(settings) as store:
            hits = store.sparse_search(query, limit=limit, categories=categories)
        print(f"Запрос: {query} | режим: sparse (BM25)\n")
        for number, hit in enumerate(hits, start=1):
            _print_hit(number, hit.score, hit.source_file, hit.section, hit.source_url, hit.text)
        if not hits:
            print("Ничего не найдено")
        return 0

    embedder = Embedder(settings)
    categories = [category] if category else None
    with VectorStore(settings) as store:
        if mode == "dense":
            hits = store.dense_search(
                embedder.embed_query(query), limit=limit, categories=categories
            )
            print(f"Запрос: {query} | режим: dense (pgvector)\n")
            for number, hit in enumerate(hits, start=1):
                _print_hit(number, hit.score, hit.source_file, hit.section, hit.source_url, hit.text)
            if not hits:
                print("Ничего не найдено")
            return 0

        hybrid_hits = HybridSearcher(settings, embedder, store).search(
            query, limit=limit, categories=categories
        )

    print(f"Запрос: {query} | режим: hybrid (dense + BM25 + RRF)\n")
    if not hybrid_hits:
        print("Ничего не найдено")
        return 0
    for number, hit in enumerate(hybrid_hits, start=1):
        ranks = f"dense={hit.dense_rank or '-'} sparse={hit.sparse_rank or '-'}"
        _print_hit(
            number,
            hit.rrf_score,
            hit.source_file,
            hit.section,
            hit.source_url,
            hit.text,
            extra=f" | {ranks}",
        )
    return 0


def _print_hit(
    number: int,
    score: float,
    source_file: str,
    section: str | None,
    source_url: str | None,
    text: str,
    extra: str = "",
) -> None:
    preview = " ".join(text.split())[:220]
    print(f"{number}. score={score:.4f} | {source_file} | {section}{extra}")
    print(f"   url: {source_url or '—'}")
    print(f"   {preview}...\n")


def main(argv: list[str] | None = None) -> int:
    _configure_stdout()
    settings = Settings()

    parser = argparse.ArgumentParser(description="Векторная база знаний Melke")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("index", help="индексировать документы из knowledge_base/")

    search_parser = subparsers.add_parser("search", help="поиск по базе знаний")
    search_parser.add_argument("query", help="текст запроса")
    search_parser.add_argument("--limit", type=int, default=None, help="число результатов")
    search_parser.add_argument("--category", default=None, help="фильтр по категории документа")
    search_parser.add_argument(
        "--mode",
        choices=("hybrid", "dense", "sparse"),
        default="hybrid",
        help="hybrid — dense + BM25 + RRF (по умолчанию)",
    )

    args = parser.parse_args(argv)

    if args.command == "index":
        return index_knowledge_base(settings)
    return search_knowledge_base(
        settings,
        args.query,
        limit=args.limit or settings.search_limit,
        category=args.category,
        mode=args.mode,
    )


if __name__ == "__main__":
    sys.exit(main())
