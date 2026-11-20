"""Эмбеддинги Qwen3-Embedding-0.6B: документы и поисковые запросы."""

import torch
from sentence_transformers import SentenceTransformer

from rag.config import Settings

_DTYPES = {
    "float32": torch.float32,
    "float16": torch.float16,
    "bfloat16": torch.bfloat16,
}


class Embedder:
    """Обёртка над моделью эмбеддингов.

    Документы кодируются как есть, запросы — с query instruction модели.
    Векторы нормализованы, поэтому в pgvector используется косинусная метрика.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        if settings.embedding_dtype not in _DTYPES:
            raise ValueError(
                f"Неизвестный тип данных эмбеддингов: {settings.embedding_dtype}. "
                f"Доступны: {', '.join(_DTYPES)}"
            )
        try:
            self._model = SentenceTransformer(
                settings.embedding_model,
                device=settings.embedding_device,
                # На CPU bfloat16 считается заметно медленнее float32.
                model_kwargs={"dtype": _DTYPES[settings.embedding_dtype]},
            )
        except Exception as error:
            raise RuntimeError(
                f"Не удалось загрузить модель эмбеддингов {settings.embedding_model}: {error}"
            ) from error

    @property
    def model_name(self) -> str:
        return self._settings.embedding_model

    @property
    def dimension(self) -> int:
        return self._model.get_embedding_dimension()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = self._model.encode(
            texts,
            batch_size=self._settings.embedding_batch_size,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return [vector.tolist() for vector in vectors]

    def embed_query(self, query: str) -> list[float]:
        text = f"{self._settings.query_instruction}{query}"
        vector = self._model.encode(
            [text],
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return vector[0].tolist()
