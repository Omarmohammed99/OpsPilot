import hashlib
import html
import re
from dataclasses import dataclass


def clean_text(text: str) -> str:
    text = html.unescape(text).replace("\x00", " ")
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


@dataclass(frozen=True)
class DocumentChunk:
    chunk_id: str
    source_key: str
    version: str
    index: int
    content: str
    content_hash: str


def chunk_document(
    source_key: str, version: str, text: str, *, size: int = 550, overlap: int = 80
) -> list[DocumentChunk]:
    if size <= 0 or overlap < 0 or overlap >= size:
        raise ValueError("size must be positive and overlap must satisfy 0 <= overlap < size")
    cleaned = clean_text(text)
    if not cleaned:
        return []
    words = cleaned.split()
    step = size - overlap
    chunks: list[DocumentChunk] = []
    for chunk_index, start in enumerate(range(0, len(words), step)):
        content = " ".join(words[start : start + size]).strip()
        if not content:
            continue
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        stable_id = hashlib.sha256(
            f"{source_key}\0{version}\0{chunk_index}\0{digest}".encode()
        ).hexdigest()
        chunks.append(DocumentChunk(stable_id, source_key, version, chunk_index, content, digest))
        if start + size >= len(words):
            break
    return chunks
