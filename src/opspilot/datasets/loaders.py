from pathlib import Path

from opspilot.config import get_settings
from opspilot.datasets.schemas import RunbookDataset, ScenarioDataset

PUBLIC_SEED_DIR = get_settings().opspilot_data_dir / "seed" / "public"


def load_scenario_dataset(path: Path | None = None) -> ScenarioDataset:
    source = path or PUBLIC_SEED_DIR / "scenarios.json"
    return ScenarioDataset.model_validate_json(source.read_text(encoding="utf-8"))


def load_runbook_dataset(path: Path | None = None) -> RunbookDataset:
    source = path or PUBLIC_SEED_DIR / "runbooks.json"
    return RunbookDataset.model_validate_json(source.read_text(encoding="utf-8"))
