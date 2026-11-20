"""Гибридный поиск: dense (pgvector) + sparse (BM25) + reciprocal rank fusion."""

from pydantic import BaseModel

from rag.config import Settings
from rag.embeddings import Embedder
from rag.store import SearchHit, VectorStore


class HybridHit(BaseModel):
    """Чанк, найденный гибридным поиском."""

    id: int
    text: str
    source_file: str
    section: str | None
    source_url: str | None
    category: str | None
    product_name: str | None
    rrf_score: float
    dense_rank: int | None
    sparse_rank: int | None


def reciprocal_rank_fusion(
    ranked_lists: list[list[int]],
    k: int = 60,
) -> dict[int, float]:
    """RRF: score(d) = Σ 1 / (k + rank(d)) по всем спискам, где d встречается.

    Ранги считаются с единицы; k снижает влияние верхних позиций.
    """

    scores: dict[int, float] = {}
    for ranked_ids in ranked_lists:
        for rank, chunk_id in enumerate(ranked_ids, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
    return scores


class HybridSearcher:
    """Объединяет результаты векторного и лексического поиска через RRF."""

    def __init__(self, settings: Settings, embedder: Embedder, store: VectorStore) -> None:
        self._settings = settings
        self._embedder = embedder
        self._store = store

    def search(
        self,
        query: str,
        limit: int | None = None,
        categories: list[str] | None = None,
    ) -> list[HybridHit]:
        limit = limit or self._settings.search_limit
        dense_hits = self._store.dense_search(
            self._embedder.embed_query(query),
            limit=self._settings.dense_candidates,
            categories=categories,
        )
        sparse_hits = self._store.sparse_search(
            query,
            limit=self._settings.sparse_candidates,
            categories=categories,
        )

        dense_ranks = {hit.id: rank for rank, hit in enumerate(dense_hits, start=1)}
        sparse_ranks = {hit.id: rank for rank, hit in enumerate(sparse_hits, start=1)}
        fused = reciprocal_rank_fusion(
            [list(dense_ranks), list(sparse_ranks)],
            k=self._settings.rrf_k,
        )

        hits_by_id = _merge_hits(dense_hits, sparse_hits)
        ordered_ids = sorted(fused, key=lambda chunk_id: fused[chunk_id], reverse=True)

        return [
            HybridHit(
                id=chunk_id,
                text=hits_by_id[chunk_id].text,
                source_file=hits_by_id[chunk_id].source_file,
                section=hits_by_id[chunk_id].section,
                source_url=hits_by_id[chunk_id].source_url,
                category=hits_by_id[chunk_id].category,
                product_name=hits_by_id[chunk_id].product_name,
                rrf_score=round(fused[chunk_id], 6),
                dense_rank=dense_ranks.get(chunk_id),
                sparse_rank=sparse_ranks.get(chunk_id),
            )
            for chunk_id in ordered_ids[:limit]
        ]


def _merge_hits(*hit_lists: list[SearchHit]) -> dict[int, SearchHit]:
    merged: dict[int, SearchHit] = {}
    for hits in hit_lists:
        for hit in hits:
            merged.setdefault(hit.id, hit)
    return merged
