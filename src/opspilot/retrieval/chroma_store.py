"""Persistent local Chroma collections and citation-ready retrieval."""

import shutil
import tempfile
from pathlib import Path
from typing import Any

import chromadb
from chromadb.api.models.Collection import Collection
from chromadb.api.types import Documents, EmbeddingFunction

from opspilot.config import get_settings
from opspilot.datasets.schemas import KnowledgeDocumentInput
from opspilot.retrieval.chunking import chunk_document
from opspilot.retrieval.embeddings import build_embedding_function


def reset_ephemeral_chroma_directory(path: Path) -> None:
    """Remove a broken Chroma index only when it is contained in temporary storage."""
    temp_root = Path(tempfile.gettempdir()).resolve()
    target = path.resolve()
    if target == temp_root or temp_root not in target.parents:
        raise ValueError("Refusing to reset Chroma outside the temporary directory")
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)


class KnowledgeIndex:
    def __init__(
        self,
        path: Path | None = None,
        *,
        prefix: str | None = None,
        embedding_function: EmbeddingFunction[Documents] | None = None,
        mode: str | None = None,
        host: str | None = None,
        port: int | None = None,
    ) -> None:
        settings = get_settings()
        mode = mode or settings.chroma_mode
        if mode == "embedded":
            persist_path = path or settings.chroma_persist_directory
            persist_path.mkdir(parents=True, exist_ok=True)
            self.client = chromadb.PersistentClient(path=str(persist_path))
        elif mode == "http":
            self.client = chromadb.HttpClient(
                host=host or settings.chroma_host,
                port=port or settings.chroma_port,
                ssl=settings.chroma_ssl,
            )
        else:
            raise ValueError("CHROMA_MODE must be 'embedded' or 'http'")
        self.prefix = prefix or settings.chroma_collection_prefix
        self.embedding_function = embedding_function or build_embedding_function()
        self.troubleshooting = self._collection("troubleshooting_v1")
        self.error_refs = self._collection("error_refs_v1")

    def _collection(self, suffix: str) -> Collection:
        return self.client.get_or_create_collection(
            name=f"{self.prefix}_{suffix}",
            embedding_function=self.embedding_function,
            metadata={"hnsw:space": "cosine", "embedding_model": self.embedding_function.name()},
        )

    @staticmethod
    def _scalar_metadata(metadata: dict[str, Any]) -> dict[str, str | int | float | bool]:
        normalized: dict[str, str | int | float | bool] = {}
        for key, value in metadata.items():
            if value is None:
                continue
            if isinstance(value, (str, int, float, bool)):
                normalized[key] = value
            elif isinstance(value, list):
                normalized[key] = "|".join(str(item) for item in value)
            else:
                normalized[key] = str(value)
        return normalized

    def upsert_document(
        self,
        document: KnowledgeDocumentInput,
        *,
        license: str,
        source_url: str | None,
        content_hash: str,
    ) -> int:
        chunks = chunk_document(document.source_key, document.version, document.body)
        if not chunks:
            raise ValueError(f"Document {document.source_key} has no usable text")
        collection = (
            self.error_refs if document.document_type == "error_reference" else self.troubleshooting
        )
        chunk_ids = [chunk.chunk_id for chunk in chunks]
        existing = collection.get(
            where={"$and": [{"source_key": document.source_key}, {"doc_version": document.version}]}
        )
        stale_ids = sorted(set(existing["ids"]) - set(chunk_ids))
        if stale_ids:
            collection.delete(ids=stale_ids)
        collection.upsert(
            ids=chunk_ids,
            documents=[chunk.content for chunk in chunks],
            metadatas=[
                self._scalar_metadata(
                    {
                        "source_key": document.source_key,
                        "doc_version": document.version,
                        "doc_type": document.document_type,
                        "title": document.title,
                        "service_key": document.service_key,
                        "error_codes": document.error_codes,
                        "tags": document.tags,
                        "source_url": source_url,
                        "license": license,
                        "content_hash": chunk.content_hash,
                        "chunk_index": chunk.index,
                        "active": True,
                        **{f"error__{code}": True for code in document.error_codes},
                    }
                )
                for chunk in chunks
            ],
        )
        return len(chunks)

    def count(self) -> dict[str, int]:
        return {
            "troubleshooting": self.troubleshooting.count(),
            "error_refs": self.error_refs.count(),
        }

    def retrieve(
        self,
        query: str,
        *,
        service_key: str | None = None,
        error_codes: list[str] | None = None,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        if not query.strip():
            return []
        if limit < 1 or limit > 50:
            raise ValueError("limit must be between 1 and 50")
        clauses: list[dict[str, Any]] = [{"active": True}]
        if service_key:
            clauses.append({"$or": [{"service_key": service_key}, {"service_key": "*"}]})
        if error_codes:
            code_clauses = [{f"error__{code}": True} for code in error_codes]
            clauses.append(code_clauses[0] if len(code_clauses) == 1 else {"$or": code_clauses})
        where = clauses[0] if len(clauses) == 1 else {"$and": clauses}
        results: list[dict[str, Any]] = []
        for collection in (self.troubleshooting, self.error_refs):
            count = collection.count()
            if count == 0:
                continue
            response = collection.query(
                query_texts=[query], n_results=min(limit * 3, count), where=where
            )
            ids = response.get("ids", [[]])[0]
            docs = response.get("documents", [[]])[0]
            metadatas = response.get("metadatas", [[]])[0]
            distances = response.get("distances", [[]])[0]
            for chunk_id, text, metadata, distance in zip(
                ids, docs, metadatas, distances, strict=False
            ):
                results.append(
                    {"chunk_id": chunk_id, "text": text, "metadata": metadata, "distance": distance}
                )
        results.sort(key=lambda result: result["distance"])
        return results[:limit]

    def health(self) -> dict[str, Any]:
        return {"heartbeat": self.client.heartbeat(), "collections": self.count()}
