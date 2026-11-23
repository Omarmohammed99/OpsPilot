"""Offline CLI for scoring saved investigation responses against private ground truth."""

import json
from pathlib import Path
from typing import Annotated, Any

import typer

from opspilot.config import get_settings
from opspilot.evaluation.metrics import aggregate_scores, score_case

app = typer.Typer(help="Score saved OpsPilot investigations against offline ground truth")


@app.command()
def score(
    results_file: Annotated[
        Path, typer.Argument(help="JSON file containing one or more run views")
    ],
) -> None:
    """Score reports and evidence without loading labels in the investigation runtime."""
    data_root = get_settings().opspilot_data_dir
    labels_path = data_root / "eval" / "ground_truth" / "scenarios.json"
    if not labels_path.exists():
        raise typer.BadParameter(
            "Ground truth is excluded from the application image; "
            "run this command in the repository."
        )
    try:
        labels = json.loads(labels_path.read_text(encoding="utf-8"))
        raw_results: Any = json.loads(results_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise typer.BadParameter("Unable to read evaluation inputs as JSON") from exc
    case_by_id = {case["scenario_id"]: case for case in labels["cases"]}
    runs = raw_results if isinstance(raw_results, list) else [raw_results]
    scored = []
    for run in runs:
        scenario_id = run.get("scenario_id")
        if scenario_id not in case_by_id:
            raise typer.BadParameter(f"Unknown evaluation scenario: {scenario_id}")
        scored.append(score_case(case_by_id[scenario_id], run))
    typer.echo(json.dumps(aggregate_scores(scored), indent=2, sort_keys=True))


if __name__ == "__main__":
    app()
