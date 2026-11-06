"""Loaders for the synthetic HR dataset under data/synthetic/hr/."""

from __future__ import annotations

import json
from pathlib import Path

from hall_monitor.hr.models import Company, Employee, LeaveAnswer, LeaveRequest

HR_DIR = Path(__file__).resolve().parents[2] / "data" / "synthetic" / "hr"


def _load(name: str, data_dir: Path) -> object:
    return json.loads((data_dir / name).read_text())


def load_company(data_dir: Path = HR_DIR) -> Company:
    return Company.model_validate(_load("company.json", data_dir))


def load_employees(data_dir: Path = HR_DIR) -> dict[str, Employee]:
    employees = [Employee.model_validate(e) for e in _load("employees.json", data_dir)]
    return {e.id: e for e in employees}


def load_requests(data_dir: Path = HR_DIR) -> list[LeaveRequest]:
    return [LeaveRequest.model_validate(r) for r in _load("requests.json", data_dir)]


def load_answers(data_dir: Path = HR_DIR) -> dict[str, LeaveAnswer]:
    answers = [LeaveAnswer.model_validate(a) for a in _load("ground_truth.json", data_dir)]
    return {a.request_id: a for a in answers}
