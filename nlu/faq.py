"""FAQ-датасет: заготовленные вопросы и ответы из каталога faq/."""

import json
from pathlib import Path

from pydantic import BaseModel, Field

from rag.config import PROJECT_ROOT

FAQ_DIR = PROJECT_ROOT / "faq"


class FaqEntry(BaseModel):
    """Один вопрос FAQ с готовым ответом."""

    id: str
    question: str
    variants: list[str] = Field(default_factory=list)
    answer: str
    source_url: str | None = None

    @property
    def formulations(self) -> list[str]:
        """Формулировки для сопоставления: канонический вопрос и его варианты."""

        unique = dict.fromkeys([self.question, *self.variants])
        return [text.strip() for text in unique if text.strip()]


class FaqCategory(BaseModel):
    """Содержимое одного файла faq/<категория>.json."""

    category: str
    title: str
    source_doc: str
    entries: list[FaqEntry]


def load_faq(directory: Path = FAQ_DIR) -> list[FaqCategory]:
    """Читает все JSON-файлы каталога FAQ."""

    categories: list[FaqCategory] = []
    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise ValueError(f"{path.name}: некорректный JSON ({error})") from error
        categories.append(FaqCategory.model_validate(data))
    if not categories:
        raise ValueError(f"В каталоге {directory} нет файлов FAQ")
    return categories
