import pytest

from opspilot.retrieval.chunking import chunk_document, clean_text


def test_text_cleaning_and_chunking_are_stable():
    text = "<h1>Pool</h1>\x00 " + "connection timeout evidence " * 230
    assert clean_text(text).startswith("Pool connection timeout")
    chunks = chunk_document("source-a", "1", text, size=100, overlap=20)
    assert len(chunks) > 1
    assert chunks == chunk_document("source-a", "1", text, size=100, overlap=20)
    assert len({chunk.chunk_id for chunk in chunks}) == len(chunks)


@pytest.mark.parametrize(("size", "overlap"), [(0, 0), (10, 10), (10, -1)])
def test_chunk_parameters_are_validated(size, overlap):
    with pytest.raises(ValueError):
        chunk_document("x", "1", "text", size=size, overlap=overlap)
