"""CLI NLU-слоя: индексация векторов FAQ и проверка сопоставления."""

import argparse
import sys

from nlu.faq import load_faq
from nlu.store import FaqQuestion, FaqStore
from rag.config import Settings
from rag.embeddings import Embedder

# Порог подобран прогоном по FAQ-датасету: при 0.93 заготовленный ответ не
# подменяет ответ агента на реплики диалога, а промахи уходят агенту.
DEFAULT_THRESHOLD = 0.93


def _configure_stdout() -> None:
    # Windows-консоль по умолчанию не всегда в UTF-8.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")


def index_faq(settings: Settings) -> int:
    """Считает векторы вопросов FAQ и записывает их в PostgreSQL.

    Вопросы FAQ кодируются той же инструкцией, что и сообщения клиента:
    сравниваются однородные тексты (вопрос с вопросом), поэтому косинусная
    близость оказывается высокой и её легко пороговать.
    """

    categories = load_faq()
    total_entries = sum(len(category.entries) for category in categories)
    print(f"Категорий: {len(categories)}, вопросов: {total_entries}")

    embedder = Embedder(settings)
    if embedder.dimension != settings.embedding_dim:
        raise RuntimeError(
            f"Модель {embedder.model_name} отдаёт векторы размерности "
            f"{embedder.dimension}, а в конфигурации указано {settings.embedding_dim}"
        )

    questions: list[FaqQuestion] = []
    for category in categories:
        texts: list[str] = []
        owners = []
        for entry in category.entries:
            for text in entry.formulations:
                texts.append(text)
                owners.append(entry)
        vectors = embedder.embed_queries(texts)
        questions.extend(
            FaqQuestion(
                entry_id=entry.id,
                category=category.category,
                text=text,
                question=entry.question,
                answer=entry.answer,
                source_url=entry.source_url,
                embedding=vector,
            )
            for entry, text, vector in zip(owners, texts, vectors, strict=True)
        )
        print(f"[ok] {category.category}: вопросов {len(category.entries)}, формулировок {len(texts)}")

    with FaqStore(settings) as store:
        store.ensure_schema()
        written = store.replace_all(questions)
        print(
            f"Записано формулировок: {written}, вопросов в БД: {store.entry_count()}"
        )
    return 0


def search_faq(settings: Settings, query: str, threshold: float, limit: int) -> int:
    """Проверочное сопоставление сообщения с вопросами FAQ."""

    embedder = Embedder(settings)
    with FaqStore(settings) as store:
        matches = store.search(embedder.embed_query(query), threshold, limit)

    print(f"Сообщение: {query} | порог: {threshold}\n")
    if not matches:
        print("Совпадений нет — сообщение уйдёт агенту")
        return 0
    for number, match in enumerate(matches, start=1):
        print(f"{number}. score={match.score:.4f} | {match.entry_id} | {match.category}")
        print(f"   совпало с: {match.matched_text}")
        print(f"   вопрос: {match.question}")
        print(f"   {match.answer[:200]}...\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    _configure_stdout()
    settings = Settings()

    parser = argparse.ArgumentParser(description="NLU-слой FAQ Melke")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("index", help="посчитать векторы FAQ и записать в PostgreSQL")

    search_parser = subparsers.add_parser("search", help="проверить сопоставление")
    search_parser.add_argument("query", help="сообщение клиента")
    search_parser.add_argument(
        "--threshold", type=float, default=DEFAULT_THRESHOLD, help="порог косинусной близости"
    )
    search_parser.add_argument("--limit", type=int, default=3, help="число совпадений")

    args = parser.parse_args(argv)

    if args.command == "index":
        return index_faq(settings)
    return search_faq(settings, args.query, args.threshold, args.limit)


if __name__ == "__main__":
    sys.exit(main())
