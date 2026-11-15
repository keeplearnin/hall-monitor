"""Load the synthetic partner claims data (data/synthetic/partners/)."""

from __future__ import annotations

from pathlib import Path

import yaml

from hall_monitor.partners.models import Answer
from hall_monitor.partners.rules import ClaimBook

DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "synthetic" / "partners"


def _read(name: str, data_dir: Path) -> dict:
    return yaml.safe_load((data_dir / name).read_text())


def load_book(data_dir: Path = DATA_DIR) -> ClaimBook:
    return ClaimBook(
        program=_read("program.yaml", data_dir),
        partners=_read("partners.yaml", data_dir)["partners"],
        preapprovals=_read("preapprovals.yaml", data_dir)["preapprovals"],
        history=_read("history.yaml", data_dir)["history"],
        claims=_read("claims.yaml", data_dir)["claims"],
    )


def load_answers(data_dir: Path = DATA_DIR) -> dict[str, Answer]:
    return {
        k: Answer.model_validate(v) for k, v in _read("answers.yaml", data_dir)["answers"].items()
    }
