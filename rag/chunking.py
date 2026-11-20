"""Разбиение документа на чанки с сохранением контекста разделов."""

import re
from typing import Any

from langchain_text_splitters import RecursiveCharacterTextSplitter
from pydantic import BaseModel

from rag.config import PRODUCT_NAMES, Settings
from rag.documents import KnowledgeDocument

HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
SOURCE_LINE_RE = re.compile(r"^\s*Источник", re.IGNORECASE)
URL_RE = re.compile(r"https?://[^\s,;)]+")

SEPARATORS = ["\n\n", "\n", ". ", "! ", "? ", " ", ""]

# Раздел документа: путь заголовков и тело раздела.
Section = tuple[list[str], str]


class Chunk(BaseModel):
    """Фрагмент документа, готовый к индексации."""

    index: int
    text: str
    section: str | None
    source_url: str | None
    product_name: str | None
    metadata: dict[str, Any]


def split_sections(text: str) -> list[Section]:
    """Делит документ на разделы по заголовкам Markdown, сохраняя путь заголовков."""

    stack: list[tuple[int, str]] = []
    sections: list[Section] = []
    body: list[str] = []

    for line in text.splitlines():
        match = HEADING_RE.match(line)
        if match:
            sections.append(([title for _, title in stack], "\n".join(body).strip()))
            level = len(match.group(1))
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, match.group(2)))
            body = []
        else:
            body.append(line)
    sections.append(([title for _, title in stack], "\n".join(body).strip()))

    return [(path, content) for path, content in sections if content]


def extract_source_urls(content: str) -> list[str]:
    urls: list[str] = []
    for line in content.splitlines():
        if not SOURCE_LINE_RE.match(line):
            continue
        for url in URL_RE.findall(line):
            if url not in urls:
                urls.append(url.rstrip(".,;"))
    return urls


def detect_product_name(heading_path: list[str]) -> str | None:
    heading = " > ".join(heading_path)
    for product_name in PRODUCT_NAMES:
        if product_name.lower() in heading.lower():
            return product_name
    return None


def split_document(document: KnowledgeDocument, settings: Settings) -> list[Chunk]:
    """Разбивает документ на чанки. Заголовки разделов добавляются в текст каждого чанка."""

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
        separators=SEPARATORS,
        keep_separator=True,
    )

    chunks: list[Chunk] = []
    sections = split_sections(document.text)
    # Список источников документа целиком лежит в первом разделе (под заголовком H1).
    document_source_urls = extract_source_urls(sections[0][1]) if sections else []

    for heading_path, content in sections:
        header = " > ".join(heading_path)
        source_urls = extract_source_urls(content)
        pieces = (
            [content]
            if len(content) <= settings.chunk_size
            else splitter.split_text(content)
        )

        for piece in pieces:
            body = piece.strip()
            if not body:
                continue
            chunks.append(
                Chunk(
                    index=len(chunks),
                    text=f"{header}\n\n{body}" if header else body,
                    section=heading_path[-1] if heading_path else None,
                    source_url=source_urls[0] if source_urls else None,
                    product_name=detect_product_name(heading_path),
                    metadata={
                        "document_title": document.title,
                        "heading_path": heading_path,
                        "source_urls": source_urls,
                        "document_source_urls": document_source_urls,
                    },
                )
            )

    if not chunks:
        raise ValueError(f"{document.source_file}: не удалось получить ни одного чанка")
    return chunks
