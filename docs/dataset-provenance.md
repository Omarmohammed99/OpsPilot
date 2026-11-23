# Dataset Provenance and Isolation

## Agent-visible synthetic dataset

Phase 2 intentionally uses no downloaded public corpus. `data/seed/public/scenarios.json` contains eight small, deterministic synthetic IT incidents and linked assets, application log events, and work orders. `data/seed/public/runbooks.json` contains eight fictional, synthetic troubleshooting/error-reference documents. The data is generated for this project and is marked `CC-BY-4.0`; no third-party source or real operational record is used. Timestamps are fictional fixed UTC times. All operational rows are marked synthetic in PostgreSQL.

Scenario categories: database connection-pool exhaustion, missing runtime configuration after deployment, log-driven disk pressure, service dependency timeout, expired TLS certificate, stale notification queue consumer, deployment schema/version skew, and an ambiguous latency sample that should produce uncertainty/no action.

The seed is JSON plus Pydantic-validated loader models. Each ID is stable/deterministic, and `seed_version` is recorded in the template session and ingestion batch. Malformed files fail validation with a recorded failed batch; they are not silently skipped. Each scenario maps one incident to a service/asset, evidence-coded timestamped logs, prior work orders, and documents bearing relevant error codes. Runbooks guide investigation without containing the evaluation answer key.

## Evaluation-only labels

`data/eval/ground_truth/scenarios.json` has one expected root cause, relevant evidence-code list, acceptable resolution and action expectation per scenario. Runtime loader APIs do not import it; the Dockerfile only copies `data/seed/public`. Tests/evaluation can load it explicitly through the offline-only helper. Never place it in Chroma, database incident rows, runtime image, prompts, or user-facing investigation tools.

## Public datasets considered

No public dataset was downloaded. LogHub is a research/academic log collection with attribution/citation conditions, and logs may not be sanitized. Its records do not correlate with these synthetic incident/assets/work orders. It remains an optional separately licensed parser benchmark and is excluded from the core pipeline. Hugging Face mirrors are not treated as independently licensed sources; any later use requires checking that dataset card's identifier, revision, files, upstream provenance, declared license and size first.

