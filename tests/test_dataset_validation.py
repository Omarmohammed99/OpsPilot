import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from opspilot.datasets.loaders import load_runbook_dataset, load_scenario_dataset


def test_public_scenarios_are_reproducible_and_valid():
    first = load_scenario_dataset()
    second = load_scenario_dataset()
    assert first == second
    assert len(first.scenarios) >= 8
    assert all(scenario.logs for scenario in first.scenarios)
    assert all(scenario.asset.environment == "demo" for scenario in first.scenarios)


def test_public_runbooks_are_valid_and_have_attribution():
    docs = load_runbook_dataset().documents
    assert len(docs) >= 5
    assert len({(doc.source_key, doc.version) for doc in docs}) == len(docs)
    assert all(doc.body.strip() for doc in docs)


def test_invalid_scenario_fields_are_rejected(tmp_path):
    source = tmp_path / "bad.json"
    source.write_text(
        json.dumps({"seed_version": "x", "seed": 1, "scenarios": [{"scenario_id": "bad"}]})
    )
    with pytest.raises(ValidationError):
        load_scenario_dataset(source)


def test_ground_truth_is_a_separate_offline_file():
    project_root = Path(__file__).resolve().parents[1]
    path = project_root / "data" / "eval" / "ground_truth" / "scenarios.json"
    truth = json.loads(path.read_text(encoding="utf-8"))
    public_ids = {s.scenario_id for s in load_scenario_dataset().scenarios}
    assert {case["scenario_id"] for case in truth["cases"]} == public_ids
    assert all(
        "expected_root_cause" not in scenario.model_dump()
        for scenario in load_scenario_dataset().scenarios
    )
    dockerfile = project_root / "Dockerfile"
    assert "COPY data/eval" not in dockerfile.read_text(encoding="utf-8")
    assert "COPY data/seed/public" in dockerfile.read_text(encoding="utf-8")
