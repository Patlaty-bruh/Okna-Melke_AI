"""CLI NLU-слоя: индексация векторов FAQ и проверка сопоставления."""

import argparse
import sys

from nlu.index import ensure_indexed, index_faq
from nlu.store import FaqStore
from rag.config import Settings
from rag.embeddings import get_embedder

# Порог подобран прогоном по FAQ-датасету: при 0.93 заготовленный ответ не
# подменяет ответ агента на реплики диалога, а промахи уходят агенту.
DEFAULT_THRESHOLD = 0.93


def _configure_stdout() -> None:
    # Windows-консоль по умолчанию не всегда в UTF-8.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")


def run_index(settings: Settings, force: bool) -> int:
    """Считает векторы FAQ и записывает их в PostgreSQL."""

    if force:
        print(f"Проиндексировано формулировок: {index_faq(settings)}")
    elif ensure_indexed(settings):
        print("Индекс отсутствовал или устарел — пересчитан")
    else:
        print("Индекс актуален, пересчёт не требуется (--force — пересчитать)")
    with FaqStore(settings) as store:
        print(
            f"В БД: формулировок {store.formulation_count()}, "
            f"вопросов {store.entry_count()}"
        )
    return 0


def search_faq(settings: Settings, query: str, threshold: float, limit: int) -> int:
    """Проверочное сопоставление сообщения с вопросами FAQ."""

    embedder = get_embedder(settings)
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
    index_parser = subparsers.add_parser(
        "index", help="посчитать векторы FAQ и записать в PostgreSQL"
    )
    index_parser.add_argument(
        "--force", action="store_true", help="пересчитать, даже если датасет не менялся"
    )

    search_parser = subparsers.add_parser("search", help="проверить сопоставление")
    search_parser.add_argument("query", help="сообщение клиента")
    search_parser.add_argument(
        "--threshold", type=float, default=DEFAULT_THRESHOLD, help="порог косинусной близости"
    )
    search_parser.add_argument("--limit", type=int, default=3, help="число совпадений")

    args = parser.parse_args(argv)

    if args.command == "index":
        return run_index(settings, args.force)
    return search_faq(settings, args.query, args.threshold, args.limit)


if __name__ == "__main__":
    sys.exit(main())
