"""Чтение Markdown-документов базы знаний и их метаданные."""

import hashlib
import re
from pathlib import Path

from pydantic import BaseModel

from rag.config import Settings

SUPPORTED_SUFFIXES = {".md", ".markdown"}

TITLE_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)


class KnowledgeDocument(BaseModel):
    """Исходный документ базы знаний."""

    source_file: str
    title: str
    category: str
    content_hash: str
    text: str


def compute_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def extract_title(text: str, fallback: str) -> str:
    match = TITLE_RE.search(text)
    return match.group(1).strip() if match else fallback


def read_document(path: Path, source_file: str, category: str) -> KnowledgeDocument:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"{source_file}: документ не содержит текста")
    return KnowledgeDocument(
        source_file=source_file,
        title=extract_title(text, fallback=path.stem),
        category=category,
        content_hash=compute_hash(text),
        text=text,
    )


def load_documents(settings: Settings) -> tuple[list[KnowledgeDocument], list[str]]:
    """Читает все поддерживаемые Markdown-файлы базы знаний.

    Возвращает документы и список ошибок чтения по отдельным файлам:
    ошибка одного файла не мешает обработать остальные.
    """

    base_dir = settings.knowledge_base_dir
    if not base_dir.is_dir():
        raise FileNotFoundError(f"Директория базы знаний не найдена: {base_dir}")

    documents: list[KnowledgeDocument] = []
    errors: list[str] = []
    for path in sorted(base_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_SUFFIXES:
            continue
        if path.name in settings.excluded_files:
            continue
        source_file = path.relative_to(base_dir).as_posix()
        try:
            documents.append(
                read_document(path, source_file, settings.category_for(source_file))
            )
        except Exception as error:
            errors.append(f"{source_file}: {type(error).__name__}: {error}")

    if not documents and not errors:
        raise FileNotFoundError(f"В директории {base_dir} не найдено Markdown-документов")
    return documents, errors
