"""Deterministic local embedding backend and configurable OpenAI-compatible option."""

from typing import Any

import numpy as np
from chromadb.api.types import Documents, EmbeddingFunction, Embeddings
from sklearn.feature_extraction.text import HashingVectorizer

from opspilot.config import get_settings


class LocalHashEmbeddingFunction(EmbeddingFunction[Documents]):
    """Small deterministic lexical vectors; useful offline and for the synthetic corpus."""

    def __init__(self, dimensions: int = 1024) -> None:
        self.dimensions = dimensions
        self.vectorizer = HashingVectorizer(
            n_features=dimensions,
            alternate_sign=False,
            norm="l2",
            ngram_range=(1, 2),
            lowercase=True,
            token_pattern=r"(?u)\b\w+\b",
        )

    def __call__(self, input: Documents) -> Embeddings:
        vectors = self.vectorizer.transform(input).astype(np.float32).toarray()
        return vectors.tolist()

    @staticmethod
    def name() -> str:
        return "opspilot-local-hash-v1"

    def get_config(self) -> dict[str, Any]:
        return {"dimensions": self.dimensions}

    @staticmethod
    def build_from_config(config: dict[str, Any]) -> "LocalHashEmbeddingFunction":
        return LocalHashEmbeddingFunction(dimensions=int(config["dimensions"]))


def build_embedding_function() -> EmbeddingFunction[Documents]:
    settings = get_settings()
    if settings.embedding_provider == "local_hash":
        return LocalHashEmbeddingFunction(settings.embedding_dimensions)
    if settings.embedding_provider == "openai":
        try:
            from chromadb.utils.embedding_functions import OpenAIEmbeddingFunction
        except ImportError as exc:  # pragma: no cover - depends on optional provider install
            raise RuntimeError("OpenAI-compatible embedding provider is unavailable") from exc
        if not settings.openai_embedding_model:
            raise ValueError("OPENAI_EMBEDDING_MODEL is required for EMBEDDING_PROVIDER=openai")
        return OpenAIEmbeddingFunction(
            api_key=(
                settings.openai_api_key.get_secret_value() if settings.openai_api_key else None
            ),
            model_name=settings.openai_embedding_model,
            api_base=settings.openai_base_url,
        )
    raise ValueError(f"Unsupported EMBEDDING_PROVIDER: {settings.embedding_provider}")
