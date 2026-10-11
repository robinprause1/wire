"""One Haystack collection shared by all desks.

Indexing is cleaner, then dense embeddings, then sparse term counts, then the
writer. Search is BM25 plus the dense retriever, fused with reciprocal rank
fusion. Jev ranks that shortlist later. The desk id is a payload field, so one
collection serves every desk and a query never crosses desks.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import torch
from sentence_transformers import SentenceTransformer

from haystack import Document, Pipeline, component
from haystack.components.joiners import DocumentJoiner
from haystack.components.preprocessors import DocumentCleaner
from haystack.components.retrievers.in_memory import InMemoryBM25Retriever, InMemoryEmbeddingRetriever
from haystack.components.writers import DocumentWriter
from haystack.dataclasses import SparseEmbedding
from haystack.document_stores.in_memory import InMemoryDocumentStore
from haystack.document_stores.types import DuplicatePolicy, FilterPolicy
from haystack_integrations.components.retrievers.qdrant import QdrantEmbeddingRetriever, QdrantSparseEmbeddingRetriever
from haystack_integrations.document_stores.qdrant import QdrantDocumentStore

from intel.connectors.base import Item
from intel.embed import embed

EMBEDDER_MODEL = "intfloat/multilingual-e5-small"
EMBEDDER_DIM = 384
RECALL = 12
CONTENT_CHARS = 2000

Tagger = Callable[[str, str, str], list[str]]


@dataclass(frozen=True)
class Retrieved:
    id: str
    source: str
    title: str
    text: str
    url: str
    published_at: str
    score: float


def document_id(desk: str, source: str, external_id: str) -> str:
    return hashlib.sha256(f"{desk}\n{source}\n{external_id}".encode()).hexdigest()


def use_lexicon() -> bool:
    return os.environ.get("WIRE_EMBEDDER") == "lexicon"


def embedder_name() -> str:
    return "lexicon" if use_lexicon() else EMBEDDER_MODEL


class WireIndex:
    def __init__(self, store: InMemoryDocumentStore | QdrantDocumentStore, *, sparse: bool) -> None:
        self.store = store
        self.sparse = sparse
        self.index_pipeline = _index_pipeline(store, sparse)
        self.query_pipeline = _query_pipeline(store, sparse)

    @classmethod
    def open(cls, path: Path | None) -> WireIndex:
        if use_lexicon() or path is None:
            memory = InMemoryDocumentStore(embedding_similarity_function="cosine")
            return cls(memory, sparse=False)
        path.mkdir(parents=True, exist_ok=True)
        vector = QdrantDocumentStore(
            path=str(path),
            index="wire",
            embedding_dim=EMBEDDER_DIM,
            on_disk=True,
            on_disk_payload=True,
            use_sparse_embeddings=True,
            sparse_idf=True,
            similarity="cosine",
            recreate_index=False,
            wait_result_from_api=True,
            force_disable_check_same_thread=True,
            progress_bar=False,
            return_embedding=False,
            payload_fields_to_index=[
                {"field_name": "meta.desk", "field_schema": "keyword"},
                {"field_name": "meta.source", "field_schema": "keyword"},
                {"field_name": "meta.ingested_ts", "field_schema": "integer"},
            ],
        )
        return cls(vector, sparse=True)

    def ingest(self, desk: str, kind: str, items: list[Item], known: set[str], tagger: Tagger) -> tuple[int, list[str]]:
        """Write the items of one source. Tags from the desk's picker are appended so retrieval sees them."""
        fresh: list[Document] = []
        ids: list[str] = []
        for item in items:
            doc_id = document_id(desk, item.source, item.external_id)
            if doc_id in known:
                continue
            content = f"{item.title}\n{item.body}".strip()
            if not content:
                continue
            tags = tagger(kind, item.title, content)
            if tags:
                content = f"{content}\nTags: {', '.join(tags)}"
            now = datetime.now(UTC)
            fresh.append(
                Document(
                    id=doc_id,
                    content=content[:CONTENT_CHARS],
                    meta={
                        "desk": desk,
                        "source": item.source,
                        "kind": kind,
                        "title": item.title[:300],
                        "url": item.url[:500],
                        "published_at": item.published_at[:80],
                        "ingested_at": now.isoformat(),
                        "ingested_ts": int(now.timestamp()),
                        "tags": ",".join(tags),
                    },
                )
            )
            ids.append(doc_id)
        if not fresh:
            return 0, []
        self.index_pipeline.run({"cleaner": {"documents": fresh}})
        return len(fresh), ids

    def search(self, question: str, *, desk: str, limit: int = 8, within_hours: float | None = None) -> list[Retrieved]:
        filters = _filters(desk, within_hours)
        if self.sparse:
            payload: dict = {
                "text_embedder": {"text": question},
                "lexical": {"text": question},
                "bm25": {"top_k": RECALL},
                "dense": {"top_k": RECALL},
            }
        else:
            payload = {
                "text_embedder": {"text": question},
                "bm25": {"query": question, "top_k": RECALL},
                "dense": {"top_k": RECALL},
            }
        payload["bm25"]["filters"] = filters
        payload["dense"]["filters"] = filters
        result = self.query_pipeline.run(payload)
        documents = result["joiner"]["documents"]
        found = [_retrieved(document) for document in documents]
        found.sort(key=lambda hit: hit.score, reverse=True)
        return found[:limit]

    def latest(self, desk: str, limit: int = 80) -> list[dict[str, object]]:
        documents = list(self.store.filter_documents(filters=_filters(desk, None)))
        documents.sort(key=lambda document: str((document.meta or {}).get("ingested_at") or ""), reverse=True)
        rows = []
        for document in documents[:limit]:
            meta = document.meta or {}
            text = document.content or ""
            rows.append(
                {
                    "id": document.id,
                    "source": meta.get("source") or "",
                    "title": meta.get("title") or text[:120],
                    "snippet": text[:220],
                    "url": meta.get("url") or "",
                    "publishedAt": meta.get("published_at") or "",
                    "ingestedAt": meta.get("ingested_at") or "",
                }
            )
        return rows

    def count(self, desk: str | None = None) -> int:
        if desk is None:
            return int(self.store.count_documents())
        return len(self.store.filter_documents(filters=_filters(desk, None)))

    def documents(self, desk: str, within_hours: float) -> list[dict[str, object]]:
        """Everything a desk ingested inside its window, as plain rows for the pickers."""
        rows: list[dict[str, object]] = []
        for document in self.store.filter_documents(filters=_filters(desk, within_hours)):
            meta = document.meta or {}
            raw_tags = meta.get("tags") or ""
            tags = [part for part in raw_tags.split(",") if part] if isinstance(raw_tags, str) else [str(part) for part in raw_tags]
            rows.append(
                {
                    "source": str(meta.get("source") or ""),
                    "kind": str(meta.get("kind") or ""),
                    "title": str(meta.get("title") or ""),
                    "text": document.content or "",
                    "url": str(meta.get("url") or ""),
                    "tags": tags,
                }
            )
        return rows


def _filters(desk: str, within_hours: float | None) -> dict:
    conditions: list[dict] = [{"field": "meta.desk", "operator": "==", "value": desk}]
    if within_hours is not None:
        cutoff = int((datetime.now(UTC) - timedelta(hours=within_hours)).timestamp())
        conditions.append({"field": "meta.ingested_ts", "operator": ">=", "value": cutoff})
    if len(conditions) == 1:
        return conditions[0]
    return {"operator": "AND", "conditions": conditions}


def _retrieved(document: Document) -> Retrieved:
    meta = document.meta or {}
    return Retrieved(
        id=str(document.id),
        source=str(meta.get("source") or ""),
        title=str(meta.get("title") or ""),
        text=(document.content or "")[:480],
        url=str(meta.get("url") or ""),
        published_at=str(meta.get("published_at") or ""),
        score=float(document.score or 0),
    )


def _index_pipeline(store: InMemoryDocumentStore | QdrantDocumentStore, sparse: bool) -> Pipeline:
    pipeline = Pipeline()
    pipeline.add_component("cleaner", DocumentCleaner(keep_id=True))
    pipeline.add_component("embedder", _document_embedder())
    pipeline.add_component("writer", DocumentWriter(document_store=store, policy=DuplicatePolicy.OVERWRITE))
    pipeline.connect("cleaner.documents", "embedder.documents")
    if sparse:
        pipeline.add_component("lexical", LexicalDocumentEmbedder())
        pipeline.connect("embedder.documents", "lexical.documents")
        pipeline.connect("lexical.documents", "writer.documents")
    else:
        pipeline.connect("embedder.documents", "writer.documents")
    return pipeline


def _query_pipeline(store: InMemoryDocumentStore | QdrantDocumentStore, sparse: bool) -> Pipeline:
    pipeline = Pipeline()
    pipeline.add_component("text_embedder", _text_embedder())
    pipeline.add_component("joiner", DocumentJoiner(join_mode="reciprocal_rank_fusion", top_k=RECALL))
    if sparse:
        assert isinstance(store, QdrantDocumentStore)
        pipeline.add_component("lexical", LexicalTextEmbedder())
        pipeline.add_component(
            "bm25",
            QdrantSparseEmbeddingRetriever(document_store=store, filter_policy=FilterPolicy.MERGE),
        )
        pipeline.add_component(
            "dense",
            QdrantEmbeddingRetriever(document_store=store, filter_policy=FilterPolicy.MERGE),
        )
        pipeline.connect("text_embedder.embedding", "dense.query_embedding")
        pipeline.connect("lexical.sparse_embedding", "bm25.query_sparse_embedding")
    else:
        assert isinstance(store, InMemoryDocumentStore)
        pipeline.add_component(
            "bm25",
            InMemoryBM25Retriever(document_store=store, filter_policy=FilterPolicy.MERGE),
        )
        pipeline.add_component(
            "dense",
            InMemoryEmbeddingRetriever(document_store=store, filter_policy=FilterPolicy.MERGE),
        )
        pipeline.connect("text_embedder.embedding", "dense.query_embedding")
    pipeline.connect("bm25.documents", "joiner.documents")
    pipeline.connect("dense.documents", "joiner.documents")
    return pipeline


def _document_embedder() -> LexiconDocumentEmbedder | SentenceDocumentEmbedder:
    if use_lexicon():
        return LexiconDocumentEmbedder()
    return SentenceDocumentEmbedder()


def _text_embedder() -> LexiconTextEmbedder | SentenceTextEmbedder:
    if use_lexicon():
        return LexiconTextEmbedder()
    return SentenceTextEmbedder()


def sparse_embed(text: str) -> SparseEmbedding:
    counts: dict[int, float] = {}
    for raw in text.lower().split():
        token = "".join(char for char in raw if char.isalnum() or char in "$-")
        if len(token) < 2:
            continue
        slot = int.from_bytes(hashlib.blake2s(token.encode(), digest_size=4).digest(), "little") % 2_147_483_647 or 1
        counts[slot] = counts.get(slot, 0.0) + 1.0
    if not counts:
        return SparseEmbedding(indices=[0], values=[0.0])
    ordered = sorted(counts)
    return SparseEmbedding(indices=ordered, values=[counts[slot] for slot in ordered])


@component
class LexicalDocumentEmbedder:
    @component.output_types(documents=list[Document])
    def run(self, documents: list[Document]) -> dict:
        embedded = [replace(document, sparse_embedding=sparse_embed(document.content or "")) for document in documents]
        return {"documents": embedded}


@component
class LexicalTextEmbedder:
    @component.output_types(sparse_embedding=SparseEmbedding)
    def run(self, text: str) -> dict:
        return {"sparse_embedding": sparse_embed(text)}


@component
class LexiconDocumentEmbedder:
    @component.output_types(documents=list[Document])
    def run(self, documents: list[Document]) -> dict:
        embedded = [replace(document, embedding=embed(document.content or "")) for document in documents]
        return {"documents": embedded}


@component
class LexiconTextEmbedder:
    @component.output_types(embedding=list[float])
    def run(self, text: str) -> dict:
        return {"embedding": embed(text)}


@component
class SentenceDocumentEmbedder:
    @component.output_types(documents=list[Document])
    def run(self, documents: list[Document]) -> dict:
        vectors = _sentence_vectors([f"passage: {document.content or ''}" for document in documents])
        embedded = [replace(document, embedding=vector) for document, vector in zip(documents, vectors)]
        return {"documents": embedded}


@component
class SentenceTextEmbedder:
    @component.output_types(embedding=list[float])
    def run(self, text: str) -> dict:
        return {"embedding": _sentence_vectors([f"query: {text}"])[0]}


_sentence_model: object | None = None


def _sentence_vectors(texts: list[str]) -> list[list[float]]:
    global _sentence_model
    if not isinstance(_sentence_model, SentenceTransformer):
        _sentence_model = SentenceTransformer(EMBEDDER_MODEL, device=_device())
    encoded = _sentence_model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
    return [row.tolist() for row in encoded]


def _device() -> str:
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"
