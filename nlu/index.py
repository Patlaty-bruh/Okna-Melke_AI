"""Индексация FAQ: векторы формулировок вопросов в PostgreSQL."""

import logging

from nlu.faq import FaqCategory, dataset_hash, load_faq
from nlu.store import FaqQuestion, FaqStore
from rag.config import Settings
from rag.embeddings import get_embedder

logger = logging.getLogger(__name__)


def build_questions(
    settings: Settings, categories: list[FaqCategory]
) -> list[FaqQuestion]:
    """Считает векторы всех формулировок FAQ.

    Вопросы кодируются той же инструкцией, что и сообщения клиента: сравниваются
    однородные тексты (вопрос с вопросом), поэтому косинусная близость сопоставима.
    """
    embedder = get_embedder(settings)
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
    return questions


def index_faq(settings: Settings) -> int:
    """Пересчитывает векторы FAQ и полностью заменяет содержимое таблицы."""

    categories = load_faq()
    questions = build_questions(settings, categories)
    with FaqStore(settings) as store:
        store.ensure_schema()
        written = store.replace_all(questions, dataset_hash(categories))
        logger.info("FAQ: проиндексировано формулировок %d", written)
    return written


def ensure_indexed(settings: Settings) -> bool:
    """Индексирует FAQ, если таблицы нет или датасет изменился.

    Вызывается при старте приложения в фоне: после пересоздания тома БД индекс
    восстанавливается сам, без ручных команд. Возвращает True, если индексация выполнена.
    """

    categories = load_faq()
    expected = dataset_hash(categories)
    with FaqStore(settings) as store:
        store.ensure_schema()
        if store.dataset_hash() == expected and store.formulation_count():
            logger.info(
                "FAQ: индекс актуален, формулировок %d", store.formulation_count()
            )
            return False

    logger.info("FAQ: индекс отсутствует или устарел, строю заново")
    questions = build_questions(settings, categories)
    with FaqStore(settings) as store:
        store.replace_all(questions, expected)
        logger.info("FAQ: проиндексировано формулировок %d", len(questions))
    return True
