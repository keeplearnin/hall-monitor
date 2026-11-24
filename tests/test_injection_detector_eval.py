"""The offline injection-detector eval (plan step 2): its set, scoring and published result."""

from __future__ import annotations

import json
import re

import pytest
import yaml

from hall_monitor.evals import gate, injection_detector
from hall_monitor.evals.injection_detector import (
    HELDOUT_SHA256,
    RESULTS_DIR,
    SET_PATH,
    SetError,
    evaluate,
    heldout_sha256,
    load_cases,
    wilson,
)
from hall_monitor.registry import load_approvals
from tests.fakes import TEST_REGISTRY_DIR

CASES = load_cases()
PUBLISHED = json.loads((RESULTS_DIR / "results.json").read_text())


@pytest.fixture(scope="module")
def fresh():
    return injection_detector.run()


def test_the_set_has_attacks_and_benign_text_in_both_splits():
    for split in ("dev", "heldout"):
        labels = [c["label"] for c in CASES if c["split"] == split]
        assert labels.count("attack") >= 20 and labels.count("benign") >= 20


def test_the_set_is_synthetic():
    text = SET_PATH.read_text()
    assert text.startswith("# SYNTHETIC")
    emails = re.findall(r"[\w.+-]+@([\w-]+\.[\w.]+)", text)
    assert all(domain == "example.com" for domain in emails), emails


def test_the_heldout_cases_are_pinned():
    """Changing them needs a new digest here and a reason in data/synthetic/README.md, whose
    change table names the digest it is logging."""
    assert heldout_sha256(CASES) == HELDOUT_SHA256
    log = (SET_PATH.parents[1] / "README.md").read_text()
    assert f"held-out digest `{HELDOUT_SHA256[:12]}`" in log


def test_the_published_report_is_the_published_result():
    """The report is the evidence the docs link to."""
    assert injection_detector.report(PUBLISHED) == (RESULTS_DIR / "report.md").read_text()


def test_the_published_result_is_what_the_detector_gives_now(fresh):
    for key in (
        "summary",
        "by_family",
        "by_surface",
        "missed_attacks",
        "false_alarms",
        "caught_only_by_tool_names",
        "rows",
    ):
        assert fresh[key] == PUBLISHED[key], key
    meta = PUBLISHED["meta"]
    assert meta["mode"] == "offline-detector" and meta["agents"] == []
    assert meta["set_sha256"] == fresh["meta"]["set_sha256"]
    assert meta["detector_sha256"] == fresh["meta"]["detector_sha256"]
    assert meta["heldout_sha256"] == HELDOUT_SHA256


def test_the_published_result_meets_the_gate():
    ok, results = gate.run_gate(approvals=load_approvals(TEST_REGISTRY_DIR / "approvals.yaml"))
    [check] = [r for r in results if r.name.startswith("Injection detector")]
    assert check.state == "pass", check.failures + check.notes


def test_the_score_comes_from_the_detector(monkeypatch):
    monkeypatch.setattr(injection_detector, "detect_in_texts", lambda texts: ["always"])
    s = evaluate(CASES)["summary"]
    assert s["recall"] == 1.0 and s["false_positive_rate"] == 1.0
    monkeypatch.setattr(injection_detector, "detect_in_texts", lambda texts: [])
    s = evaluate(CASES)["summary"]
    assert s["recall"] == 0.0 and s["false_positive_rate"] == 0.0


def test_the_result_names_cases_by_id_not_text(fresh):
    texts = {c["text"] for c in CASES}
    dumped = json.dumps({k: v for k, v in fresh.items() if k != "meta"})
    assert not any(t in dumped for t in texts)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda c: c[1].update(id=c[0]["id"]), "duplicate id"),
        (lambda c: c[0].update(label="maybe"), "label"),
        (lambda c: c[0].update(split="test"), "split"),
        (lambda c: c[0].update(surface="email"), "surface"),
        (lambda c: c[0].update(text="  "), "empty text"),
        (lambda c: c[0].update(extra=1), "exactly"),
        (lambda c: c[0].update(label="benign"), "prefix"),
        (lambda c: c[1].update(family=c[0]["family"]), "both labels"),
    ],
)
def test_a_malformed_set_is_refused(tmp_path, change, message):
    attack = next(c for c in CASES if c["label"] == "attack")
    benign = next(c for c in CASES if c["label"] == "benign")
    cases = [dict(attack), dict(benign)]
    change(cases)
    path = tmp_path / "set.yaml"
    path.write_text(yaml.safe_dump({"cases": cases}))
    with pytest.raises(SetError, match=message):
        load_cases(path)


def test_wilson_interval():
    assert wilson(0, 0) is None
    low, high = wilson(5, 10)
    assert low == pytest.approx(0.237, abs=0.001) and high == pytest.approx(0.763, abs=0.001)
    assert wilson(0, 30)[0] == 0.0 and wilson(30, 30)[1] == 1.0
