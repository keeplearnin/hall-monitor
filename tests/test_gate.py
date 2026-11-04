"""The eval gate: thresholds, unsafe routes, missing results and stale results."""

import json

import yaml

from hall_monitor.evals.gate import run_gate, unsafe_routes
from hall_monitor.registry import load_approvals


def _setup(tmp_path, summary, agents=None, runs=(), stale="warn", required=True, extra=None):
    results = tmp_path / "results.json"
    meta = {"agents": agents if agents is not None else _current_agents()}
    results.write_text(json.dumps({"meta": meta, "summary": summary, "runs": list(runs)}))
    spec = {"name": "check", "results": str(results), "required": required, "min": {"score": 0.9}}
    spec |= extra or {}
    config = tmp_path / "gate.yaml"
    config.write_text(yaml.safe_dump({"stale_results": stale, "checks": [spec]}))
    return config


def _current_agents():
    return [
        {"agent_id": a, "agent_version": v["version"], "manifest_digest": v["manifest_digest"]}
        for a, v in load_approvals().items()
    ]


def test_a_score_at_or_above_threshold_passes(tmp_path):
    ok, [result] = run_gate(_setup(tmp_path, {"score": 0.9}))
    assert ok and result.state == "pass"


def test_a_score_below_threshold_fails(tmp_path):
    ok, [result] = run_gate(_setup(tmp_path, {"score": 0.89}))
    assert not ok and result.state == "fail" and "score = 0.89 (min 0.9)" in result.failures


def test_a_missing_metric_fails(tmp_path):
    ok, _ = run_gate(_setup(tmp_path, {}))
    assert not ok


def test_stale_results_are_flagged(tmp_path):
    old = [{"agent_id": "coverage_check", "agent_version": "0.1.0", "manifest_digest": "old"}]
    ok, [result] = run_gate(_setup(tmp_path, {"score": 1.0}, agents=old, stale="warn"))
    assert ok and result.state == "stale" and "coverage_check 0.1.0" in result.notes[0]
    ok, [result] = run_gate(_setup(tmp_path, {"score": 1.0}, agents=old, stale="fail"))
    assert not ok and result.state == "fail"


def test_results_without_agent_versions_count_as_stale(tmp_path):
    ok, [result] = run_gate(_setup(tmp_path, {"score": 1.0}, agents=[], stale="warn"))
    assert ok and result.state == "stale"
    ok, [result] = run_gate(_setup(tmp_path, {"score": 1.0}, agents=[], stale="fail"))
    assert not ok and result.state == "fail"


def test_missing_results_fail_only_when_required(tmp_path):
    config = tmp_path / "gate.yaml"
    spec = {"name": "x", "results": str(tmp_path / "nope.json"), "max": {"errors": 0}}
    config.write_text(
        yaml.safe_dump({"stale_results": "warn", "checks": [spec | {"required": False}]})
    )
    assert run_gate(config)[0]
    config.write_text(
        yaml.safe_dump({"stale_results": "warn", "checks": [spec | {"required": True}]})
    )
    assert not run_gate(config)[0]


def test_unsafe_routes_count_only_claims_that_needed_a_person():
    def run(claim_id, route):
        return {"score": {}, "result": {"claim_id": claim_id, "routing": {"route": route}}}

    data = {
        "runs": [
            run("claim_004", "auto"),  # a denial routed automatically: unsafe
            run("claim_004", "adjuster"),
            run("claim_001", "adjuster"),  # an extra review: not unsafe
            run("claim_001", "auto"),
        ]
    }
    assert unsafe_routes(data) == 1


def test_published_results_pass_the_gate():
    ok, results = run_gate()
    assert ok, [(r.name, r.failures) for r in results if r.state == "fail"]
