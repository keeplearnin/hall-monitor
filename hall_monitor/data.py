"""Loaders for the synthetic dataset under data/synthetic/."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

from hall_monitor.models import ClaimSubmission, GroundTruth, PolicyRecord

DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "synthetic"


def load_claim(path: Path) -> ClaimSubmission:
    return ClaimSubmission.model_validate_json(path.read_text())


def iter_claims(data_dir: Path = DATA_DIR) -> Iterator[ClaimSubmission]:
    for path in sorted((data_dir / "claims").glob("*.json")):
        yield load_claim(path)


def load_ground_truth(claim_id: str, data_dir: Path = DATA_DIR) -> GroundTruth:
    path = data_dir / "ground_truth" / f"{claim_id}.json"
    return GroundTruth.model_validate_json(path.read_text())


def load_policy_records(data_dir: Path = DATA_DIR) -> dict[str, PolicyRecord]:
    raw = json.loads((data_dir / "policies" / "policy_records.json").read_text())
    records = [PolicyRecord.model_validate(r) for r in raw]
    return {r.policy_number: r for r in records}


def load_policy_wording(data_dir: Path = DATA_DIR) -> str:
    return (data_dir / "policies" / "wayfarer_travel_protection_v1.md").read_text()
