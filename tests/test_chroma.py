from opspilot.datasets.loaders import load_runbook_dataset
from opspilot.datasets.schemas import KnowledgeDocumentInput
from opspilot.retrieval.chroma_store import KnowledgeIndex
from opspilot.retrieval.embeddings import LocalHashEmbeddingFunction


def make_index(path):
    return KnowledgeIndex(path, prefix="test", embedding_function=LocalHashEmbeddingFunction(1024))


def test_chroma_upsert_retrieval_metadata_filter_and_persistence(chroma_path):
    index = make_index(chroma_path)
    documents = load_runbook_dataset().documents
    for document in documents:
        index.upsert_document(
            document, license="CC-BY-4.0", source_url=None, content_hash="test-hash"
        )
    before = index.count()
    for document in documents:
        index.upsert_document(
            document, license="CC-BY-4.0", source_url=None, content_hash="test-hash"
        )
    assert index.count() == before

    matches = index.retrieve(
        "database connection pool active idle acquisition timeout", service_key="checkout-api"
    )
    assert matches
    assert matches[0]["metadata"]["source_key"] == "runbook.db-pool"
    assert matches[0]["metadata"]["license"] == "CC-BY-4.0"
    assert matches[0]["chunk_id"]
    assert index.retrieve("TLS certificate expiry", error_codes=["TLS-CERT-EXPIRED"])
    assert not index.retrieve(
        "connection pool", service_key="other-service", error_codes=["NO-SUCH-CODE"]
    )

    reopened = make_index(chroma_path)
    assert reopened.count() == before
    assert reopened.health()["heartbeat"]


def test_empty_index_returns_no_documents(chroma_path):
    assert make_index(chroma_path).retrieve("anything") == []


def test_changed_source_reconciles_old_chunks(chroma_path):
    index = make_index(chroma_path)
    first = KnowledgeDocumentInput(
        source_key="mutable-test",
        version="1",
        document_type="troubleshooting_guide",
        title="Old title",
        body="Old uniquealpha phrase that should be replaced.",
    )
    changed = first.model_copy(update={"body": "New uniquebeta phrase and new instructions."})
    index.upsert_document(first, license="CC-BY-4.0", source_url=None, content_hash="old")
    old_ids = index.troubleshooting.get(where={"source_key": "mutable-test"})["ids"]
    index.upsert_document(changed, license="CC-BY-4.0", source_url=None, content_hash="new")
    new_chunks = index.troubleshooting.get(where={"source_key": "mutable-test"})
    assert len(new_chunks["ids"]) == 1
    assert new_chunks["ids"][0] not in old_ids
    assert "uniquebeta" in new_chunks["documents"][0]
