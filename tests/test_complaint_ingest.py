"""HM-26: ingestion from the CFPB complaint API, offline against a fake CFPB.

The fake serves made-up records in CFPB's shape (the company is a made-up name, to show it never
gets in) and pages them with search_after, as the real API does.
"""

import json
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest

from hall_monitor.complaints import ingest
from hall_monitor.complaints.cfpb import CFPB_SEARCH, DESK_PRODUCTS, complaint_gateway, minimise_hit
from hall_monitor.complaints.models import Complaint, Tag
from hall_monitor.complaints.store import ComplaintStore
from hall_monitor.egress import CFPB_POLICY, CfpbClient, CfpbError, guarded_client
from hall_monitor.governance.audit import AuditLog
from hall_monitor.models import ToolCall
from hall_monitor.registry import Registry
from hall_monitor.tools.gateway import PIPELINE_ACTOR
from tests.fakes import TEST_REGISTRY_DIR

REGISTRY = Registry.from_dir(TEST_REGISTRY_DIR)
START = datetime(2026, 10, 1, tzinfo=UTC)


def record(n: int, **changes) -> dict:
    received = START + timedelta(minutes=n)
    source = {
        "complaint_id": str(30000000 + n),
        "date_received": received.isoformat().replace("+00:00", "Z"),
        "date_sent_to_company": received.isoformat().replace("+00:00", "Z"),
        "product": "Credit card",
        "sub_product": "General-purpose credit card or charge card",
        "issue": "Problem with a purchase shown on your statement",
        "sub_issue": "Credit card company isn't resolving a dispute about a purchase",
        "company": "Example Lender Inc.",  # made up; must never be stored
        "state": "TX",
        "zip_code": "75201",
        "tags": None,
        "submitted_via": "Web",
        "company_response": "Closed with explanation",
        "company_public_response": None,
        "timely": "Yes",
        "date_indexed": "2026-10-02T00:00:00Z",
    }
    return {
        "_source": source | changes,
        "sort": [int(received.timestamp() * 1000), str(30000000 + n)],
    }


class FakeCfpb:
    """CFPB's search API: sorted oldest first, paged by search_after, frm ignored."""

    def __init__(self, hits: list[dict], fail: list[int] | None = None):
        self.hits = hits
        self.fail = list(fail or [])  # statuses to answer before serving
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fail:
            status = self.fail.pop(0)
            return httpx.Response(status, headers={"retry-after": "2"})
        params = request.url.params
        size = int(params["size"])
        after = params.get("search_after")
        hits = self.hits
        if after:
            ts, cid = after.split("_")
            key = (int(ts), cid)
            hits = [h for h in hits if (h["sort"][0], h["sort"][1]) > key]
        body = {
            "hits": {"total": {"value": len(self.hits)}, "hits": hits[:size]},
            "_meta": {
                "last_updated": "2026-11-11",
                "is_data_stale": False,
                "has_data_issue": False,
            },
        }
        return httpx.Response(200, json=body)


def _client(handler) -> httpx.Client:
    return guarded_client(CFPB_POLICY, transport=httpx.MockTransport(handler))


def desk(fake: FakeCfpb):
    client = CfpbClient(_client(fake), sleep=lambda s: None, clock=lambda: 0.0, jitter=lambda: 1.0)
    return complaint_gateway(REGISTRY, client), client


@pytest.fixture
def store(tmp_path):
    with ComplaintStore(tmp_path / "store.sqlite") as s:
        yield s


# The tool, at the door.


def test_the_tool_minimises_each_record_at_the_door():
    hit = record(1, zip_code="75201")
    out = minimise_hit(hit)
    assert "company" not in out["source"] and "date_indexed" not in out["source"]
    assert out["source"]["zip_code"] == "752XX"
    assert out["dropped_fields"] == ["company", "date_indexed"]
    assert out["cursor"] == f"{hit['sort'][0]}_{hit['sort'][1]}"
    assert minimise_hit(record(2, zip_code="7520"))["source"]["zip_code"] is None


def test_the_tool_asks_cfpb_for_the_desk_s_products_oldest_first(tmp_path):
    fake = FakeCfpb([record(n) for n in range(3)])
    gateway, _ = desk(fake)
    log: list[ToolCall] = []
    page = gateway.call(PIPELINE_ACTOR, CFPB_SEARCH, {"date_received_min": "2026-10-01"}, log)
    [request] = fake.requests
    assert request.method == "GET"
    params = request.url.params
    assert params["sort"] == "created_date_asc" and params["no_aggs"] == "true"
    assert params.get_list("product") == list(DESK_PRODUCTS)
    assert len(page["hits"]) == 3 and "Example Lender" not in json.dumps(page)
    assert log[0].allowed and log[0].actor == PIPELINE_ACTOR


def test_no_agent_may_call_the_cfpb_tool():
    gateway, _ = desk(FakeCfpb([]))
    log: list[ToolCall] = []
    result = gateway.call("intake", CFPB_SEARCH, {"date_received_min": "2026-10-01"}, log)
    assert result["error"].startswith("Refused") and not log[0].allowed
    assert gateway.definitions("intake") == []


@pytest.mark.parametrize(
    "arguments",
    [
        {"date_received_min": "Oct 1"},
        {"date_received_min": "2026-10-01", "size": 101},
        {"date_received_min": "2026-10-01", "search_after": "1&product=x"},
    ],
)
def test_the_tool_refuses_arguments_outside_its_shape(arguments):
    gateway, _ = desk(FakeCfpb([]))
    with pytest.raises(Exception):  # noqa: B017 - fastmcp wraps it as a ToolError
        gateway.call(PIPELINE_ACTOR, CFPB_SEARCH, arguments, [])


# The client: spacing, retries, backoff.


def test_requests_are_spaced_by_the_rate_limit():
    sleeps: list[float] = []
    times = iter([0.0, 0.2, 5.0])
    client = CfpbClient(
        _client(FakeCfpb([])), sleep=sleeps.append, clock=lambda: next(times), min_interval=1.0
    )
    for _ in range(3):
        client.search([("size", 1)])
    assert sleeps == [pytest.approx(0.8)]  # the second waited; the third came late enough


def test_429_and_5xx_are_retried_with_backoff_honouring_retry_after():
    sleeps: list[float] = []
    fake = FakeCfpb([record(1)], fail=[429, 503])
    client = CfpbClient(
        _client(fake),
        sleep=sleeps.append,
        clock=lambda: 0.0,
        jitter=lambda: 1.0,
        min_interval=0.0,
        backoff=1.0,
    )
    assert len(client.search([("size", 1)])["hits"]["hits"]) == 1
    assert client.retries == 2 and len(fake.requests) == 3
    assert sleeps == [2.0, 2.0]  # backoff 1s then 2s, Retry-After 2s raises the first


def test_retries_run_out_and_another_4xx_is_never_retried():
    client = CfpbClient(
        _client(FakeCfpb([], fail=[500] * 9)), sleep=lambda s: None, max_retries=2, min_interval=0.0
    )
    with pytest.raises(CfpbError, match="HTTP 500 after 3 tries"):
        client.search([("size", 1)])
    fake = FakeCfpb([], fail=[400])
    client = CfpbClient(_client(fake), sleep=lambda s: None, min_interval=0.0)
    with pytest.raises(CfpbError, match="HTTP 400"):
        client.search([("size", 1)])
    assert len(fake.requests) == 1


def test_timeouts_are_retried():
    calls = []

    def flaky(request):
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ConnectTimeout("slow")
        return httpx.Response(200, json={"hits": {"hits": []}})

    client = CfpbClient(_client(flaky), sleep=lambda s: None, min_interval=0.0)
    client.search([("size", 1)])
    assert client.retries == 1


@pytest.mark.parametrize("body", [b"<html>", b'{"hits": []}', b"[]"])
def test_a_response_of_the_wrong_shape_is_an_error(body):
    client = CfpbClient(_client(lambda r: httpx.Response(200, content=body)), min_interval=0.0)
    with pytest.raises(CfpbError):
        client.search([("size", 1)])


# Pulls: incremental, idempotent, quarantined, snapshotted.


def test_a_pull_stores_validated_minimised_complaints(store, tmp_path):
    fake = FakeCfpb([record(1), record(2, tags="Older American, Servicemember", zip_code="02139")])
    gateway, client = desk(fake)
    audit = AuditLog(tmp_path / "audit.sqlite")
    summary = ingest.pull(
        store,
        gateway,
        snapshots=tmp_path / "snaps",
        since=date(2026, 10, 1),
        audit=audit,
        client=client,
    )
    assert summary["status"] == "caught_up" and summary["new"] == 2
    second = store.get("30000002")
    assert second.tags == (Tag.OLDER_AMERICAN, Tag.SERVICEMEMBER) and second.zip3 == "021"
    assert "company" not in Complaint.model_fields
    assert "Example Lender" not in (tmp_path / "store.sqlite").read_bytes().decode("latin-1")
    events = [e["event"] for e in audit.entries(summary["pull_id"])]
    assert events == ["tool_call", "complaint_pull"]
    assert audit.verify().ok


def test_a_second_pull_resumes_after_the_cursor_and_repeats_nothing(store, tmp_path):
    hits = [record(n) for n in range(5)]
    fake = FakeCfpb(hits[:3])
    gateway, _ = desk(fake)
    ingest.pull(store, gateway, snapshots=tmp_path, since=date(2026, 10, 1))
    fake.hits = hits
    summary = ingest.pull(store, gateway, snapshots=tmp_path)
    assert summary["new"] == 2 and summary["unchanged"] == 0
    assert fake.requests[-1].url.params["search_after"] == summary["cursor_before"]
    assert summary["cursor_before"].endswith("_30000002")
    assert store.counts()["complaints"] == 5


def test_the_same_records_again_are_skipped_and_a_changed_one_is_a_new_version(store, tmp_path):
    fake = FakeCfpb([record(1), record(2)])
    gateway, _ = desk(fake)
    ingest.pull(store, gateway, snapshots=tmp_path, since=date(2026, 10, 1))
    fake.hits = [record(1), record(2, company_response="Closed with monetary relief")]
    again = ingest.pull(store, gateway, snapshots=tmp_path, since=date(2026, 10, 1))
    assert (again["new"], again["updated"], again["unchanged"]) == (0, 1, 1)
    assert store.get("30000002").company_response == "Closed with monetary relief"
    assert store.counts() == {"complaints": 2, "versions": 3, "quarantined": 0}


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"complaint_id": "abc"}, "complaint_id:string_pattern_mismatch"),
        ({"date_received": "yesterday"}, "date_received:datetime_from_date_parsing"),
        ({"product": None}, "product:string_type"),
        ({"tags": "Student"}, "tags:unknown_tag"),
        ({"timely": "Maybe"}, "timely:literal_error"),
        ({"issue": 7}, "issue:string_type"),
    ],
)
def test_a_bad_record_is_quarantined_and_the_pull_goes_on(store, tmp_path, changes, reason):
    fake = FakeCfpb([record(1, **changes), record(2)])
    gateway, _ = desk(fake)
    audit = AuditLog(tmp_path / "audit.sqlite")
    summary = ingest.pull(
        store, gateway, snapshots=tmp_path / "s", since=date(2026, 10, 1), audit=audit
    )
    assert (summary["new"], summary["quarantined"]) == (1, 1)
    [held] = store.quarantined()
    assert held["reason"].startswith(reason.split(":")[0]) and reason in held["reason"]
    [entry] = [e for e in audit.entries() if e["event"] == "complaint_quarantined"]
    assert entry["payload"]["reason"] == held["reason"]
    # No value from the record in the log: only names, types and a hash.
    for value in changes.values():
        if isinstance(value, str) and len(value) > 3:
            assert value not in json.dumps(entry["payload"])
    # Quarantining is idempotent too.
    again = ingest.pull(store, gateway, snapshots=tmp_path / "s", since=date(2026, 10, 1))
    assert again["quarantined"] == 0 and again["quarantined_again"] == 1


def test_a_pull_stops_at_the_desk_s_capacity(store, tmp_path):
    fake = FakeCfpb([record(n) for n in range(250)])
    gateway, _ = desk(fake)
    summary = ingest.pull(store, gateway, snapshots=tmp_path, since=date(2026, 10, 1), capacity=150)
    assert summary["status"] == "at_capacity" and summary["fetched"] == 150
    assert [r.url.params["size"] for r in fake.requests] == ["100", "50"]
    rest = ingest.pull(store, gateway, snapshots=tmp_path, capacity=150)
    assert rest["new"] == 100 and rest["status"] == "caught_up"


def test_a_failed_pull_keeps_what_it_did_and_says_why(store, tmp_path):
    fake = FakeCfpb([record(n) for n in range(150)])
    gateway, client = desk(fake)
    client.max_retries = 0
    calls = {"n": 0}
    real = fake.__call__

    def fail_second(request):
        calls["n"] += 1
        return httpx.Response(503) if calls["n"] == 2 else real(request)

    client.http = _client(fail_second)
    audit = AuditLog(tmp_path / "audit.sqlite")
    summary = ingest.pull(
        store, gateway, snapshots=tmp_path / "s", since=date(2026, 10, 1), audit=audit
    )
    assert summary["status"] == "failed" and summary["error"] == "ToolError"
    assert summary["new"] == 100 and store.cursor() == summary["cursor_after"]
    [pull_entry] = [e for e in audit.entries() if e["event"] == "complaint_pull"]
    assert pull_entry["payload"]["status"] == "failed"
    calls_logged = [e for e in audit.entries() if e["event"] == "tool_call"]
    assert len(calls_logged) == 2 and calls_logged[1]["payload"]["error"]


def test_a_snapshot_replays_offline_to_the_same_store(store, tmp_path):
    fake = FakeCfpb([record(1), record(2, issue=""), record(3)])
    gateway, _ = desk(fake)
    summary = ingest.pull(store, gateway, snapshots=tmp_path / "s", since=date(2026, 10, 1))
    directory = tmp_path / "s" / summary["pull_id"]
    with ComplaintStore(tmp_path / "replayed.sqlite") as replayed:
        counts = ingest.replay(directory, replayed)
        assert (counts["new"], counts["quarantined"]) == (2, 1)
        assert replayed.complaints() == store.complaints()


def test_a_changed_snapshot_is_refused(store, tmp_path):
    gateway, _ = desk(FakeCfpb([record(1)]))
    summary = ingest.pull(store, gateway, snapshots=tmp_path / "s", since=date(2026, 10, 1))
    page = tmp_path / "s" / summary["pull_id"] / "page-0001.json"
    page.write_text(page.read_text().replace("Credit card", "Mortgage"))
    with pytest.raises(ingest.SnapshotError, match="manifest hash"):
        ingest.load_snapshot(page.parent)


def test_pinning_copies_a_checked_snapshot_and_never_overwrites(store, tmp_path):
    gateway, _ = desk(FakeCfpb([record(1)]))
    summary = ingest.pull(store, gateway, snapshots=tmp_path / "s", since=date(2026, 10, 1))
    target = ingest.pin(summary["pull_id"], "demo", tmp_path / "s", tmp_path / "pinned")
    recorded = {"demo": ingest.snapshot_sha256(target)}
    loaded = ingest.load_pinned("demo", tmp_path / "pinned", recorded)
    assert [c.complaint_id for c in loaded] == ["30000001"]
    with pytest.raises(SystemExit):
        ingest.pin(summary["pull_id"], "demo", tmp_path / "s", tmp_path / "pinned")
    assert target.exists()


def test_the_pinned_snapshot_loads_and_is_minimised():
    complaints = ingest.load_pinned(ingest.PINNED_NAME)
    assert len(complaints) >= 200
    pinned = ingest.PINNED_SNAPSHOTS / ingest.PINNED_NAME
    for page in pinned.glob("page-*.json"):
        for hit in json.loads(page.read_text())["hits"]:
            assert "company" not in hit["source"]  # named as dropped, never kept
            assert hit["source"]["zip_code"] is None or hit["source"]["zip_code"].endswith("XX")
    assert all(c.zip3 is None or len(c.zip3) == 3 for c in complaints)


def test_the_schedule_pulls_every_interval_until_its_runs_are_done():
    sleeps: list[float] = []
    runs = ingest.run_schedule(lambda: {"status": "caught_up"}, 21600.0, 3, sleeps.append)
    assert len(runs) == 3 and sleeps == [21600.0, 21600.0]
    assert ingest.parse_every("6h") == 21600.0
    with pytest.raises(Exception):  # noqa: B017
        ingest.parse_every("5s")


def test_pull_logs_are_json_lines_with_counts(store, tmp_path, caplog):
    gateway, _ = desk(FakeCfpb([record(1)]))
    with caplog.at_level("INFO", logger="hall_monitor.complaints"):
        ingest.pull(store, gateway, snapshots=tmp_path, since=date(2026, 10, 1))
    events = [json.loads(r.message) for r in caplog.records]
    finished = [e for e in events if e["event"] == "pull_finished"]
    assert finished and finished[0]["new"] == 1 and "seconds" in finished[0]


# Review 15.


def test_cfpb_metadata_and_field_names_reach_the_audit_log_only_as_codes(store, tmp_path):
    personal = "Jane Q Public, 12 Elm St, SSN 123-45-6789"
    hit = record(1)
    hit["_source"][personal] = "x"

    class Odd(FakeCfpb):
        def __call__(self, request):
            response = super().__call__(request)
            body = response.json()
            body["_meta"]["last_updated"] = personal
            body["_meta"]["is_data_stale"] = personal
            body["hits"]["total"] = {"value": personal}
            return httpx.Response(200, json=body)

    gateway, _ = desk(Odd([hit]))
    audit = AuditLog(tmp_path / "audit.sqlite")
    summary = ingest.pull(
        store, gateway, snapshots=tmp_path / "s", since=date(2026, 10, 1), audit=audit
    )
    assert personal not in json.dumps(audit.entries()) and personal not in json.dumps(summary)
    assert summary["dropped_fields"] == ["company", "date_indexed", "unrecognised_field"]


def test_a_naive_or_numeric_date_is_quarantined(store, tmp_path):
    fake = FakeCfpb(
        [
            record(1, date_received="2026-10-01T00:05:00"),
            record(2, date_received=1790812920),
            record(3),
        ]
    )
    gateway, _ = desk(fake)
    summary = ingest.pull(store, gateway, snapshots=tmp_path, since=date(2026, 10, 1))
    assert (summary["new"], summary["quarantined"]) == (1, 2)
    assert len(store.complaints()) == 1


def test_a_cursor_out_of_range_is_refused_not_stored(store, tmp_path):
    hit = record(1)
    hit["sort"] = [9999999999999999, "30000001"]
    gateway, _ = desk(FakeCfpb([hit]))
    ingest.pull(store, gateway, snapshots=tmp_path, since=date(2026, 10, 1))
    assert store.cursor() is None
    ingest.pull(store, gateway, snapshots=tmp_path)  # doesn't raise


def test_a_page_that_does_not_advance_the_cursor_fails_the_pull(store, tmp_path):
    class IgnoresCursor(FakeCfpb):
        def __call__(self, request):
            self.requests.append(request)
            size = int(request.url.params["size"])
            return httpx.Response(
                200, json={"hits": {"total": {"value": 9}, "hits": self.hits[:size]}}
            )

    fake = IgnoresCursor([record(n) for n in range(150)])
    gateway, _ = desk(fake)
    summary = ingest.pull(store, gateway, snapshots=tmp_path, since=date(2026, 10, 1), capacity=400)
    assert summary["status"] == "failed" and summary["error"] == "cursor_did_not_advance"
    assert len(fake.requests) == 2 and summary["new"] == 100


def test_resuming_reads_from_the_day_before_the_cursor():
    """CFPB's date filter may not be in UTC: a day's margin, and search_after does the rest."""
    cursor = f"{record(1)['sort'][0]}_30000001"
    started = datetime(2026, 11, 11, tzinfo=UTC)
    assert ingest._date_min(None, cursor, started) == date(2026, 9, 30)


def test_each_pull_reports_its_own_requests_and_retries(store, tmp_path):
    fake = FakeCfpb([record(1)], fail=[503, 503])
    gateway, client = desk(fake)
    first = ingest.pull(store, gateway, snapshots=tmp_path, since=date(2026, 10, 1), client=client)
    second = ingest.pull(store, gateway, snapshots=tmp_path, client=client)
    assert (first["requests"], first["retries"]) == (3, 2)
    assert (second["requests"], second["retries"]) == (1, 0)


def test_a_restart_date_is_kept_until_a_pull_succeeds(store, tmp_path, monkeypatch):
    seen = []

    def fake_pull(store, gateway, *, since, **kwargs):
        seen.append(since)
        return {"status": "failed" if len(seen) == 1 else "caught_up"}

    monkeypatch.setattr(ingest, "pull", fake_pull)
    once = ingest.scheduled_pull(store, None, since=date(2026, 9, 1))
    ingest.run_schedule(once, 3600.0, 3, lambda s: None)
    assert seen == [date(2026, 9, 1), date(2026, 9, 1), None]


@pytest.mark.parametrize("header", ["²", "Wed, 21 Oct 2026 07:28:00 GMT", "-5"])
def test_an_odd_retry_after_is_ignored_not_an_error(header):
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(
                429, headers=[(b"retry-after", header.encode("latin-1", "replace"))]
            )
        return httpx.Response(200, json={"hits": {"hits": []}})

    client = CfpbClient(_client(handler), sleep=lambda s: None, min_interval=0.0)
    client.search([("size", 1)])
    assert client.retries == 1


def test_an_egress_refusal_is_never_retried():
    from hall_monitor.egress import EgressRefused

    calls = []

    def handler(request):
        calls.append(request)
        raise EgressRefused("refused")

    client = CfpbClient(_client(handler), sleep=lambda s: None, min_interval=0.0)
    with pytest.raises(EgressRefused):
        client.search([("size", 1)])
    assert len(calls) == 1 and client.retries == 0


def test_a_quarantine_reason_never_echoes_an_unknown_key(store, tmp_path):
    hit = minimise_hit(record(1))
    hit["source"]["Jane Q Public"] = "x"  # a snapshot written by hand, say
    page = {"hits": [hit]}
    directory = tmp_path / "snap"
    directory.mkdir()
    entry = ingest._write_page(directory, 1, page)
    (directory / "manifest.json").write_text(json.dumps({"pull_id": "p", "pages": [entry]}))
    ingest.replay(directory, store)
    [held] = store.quarantined()
    assert "Jane" not in held["reason"] and "unknown_field:extra_forbidden" in held["reason"]


def test_the_pinned_snapshot_matches_its_recorded_manifest_hash(tmp_path):
    import shutil

    copy = tmp_path / ingest.PINNED_NAME
    shutil.copytree(ingest.PINNED_SNAPSHOTS / ingest.PINNED_NAME, copy)
    page = copy / "page-0001.json"
    page.write_text(page.read_text().replace("Credit card", "Mortgage"))
    manifest = json.loads((copy / "manifest.json").read_text())
    manifest["pages"][0]["sha256"] = ingest._sha256(page.read_text())
    (copy / "manifest.json").write_text(json.dumps(manifest))
    ingest.load_snapshot(copy)  # consistent with itself
    with pytest.raises(ingest.SnapshotError, match="pinned"):
        ingest.load_pinned(ingest.PINNED_NAME, tmp_path)


def test_pin_copies_only_what_the_manifest_lists_and_checks_the_pull_id(store, tmp_path):
    gateway, _ = desk(FakeCfpb([record(1)]))
    summary = ingest.pull(store, gateway, snapshots=tmp_path / "s", since=date(2026, 10, 1))
    (tmp_path / "s" / summary["pull_id"] / "extra.txt").write_text("not listed")
    target = ingest.pin(summary["pull_id"], "demo", tmp_path / "s", tmp_path / "pinned")
    assert sorted(p.name for p in target.iterdir()) == ["manifest.json", "page-0001.json"]
    with pytest.raises(SystemExit):
        ingest.pin("../s", "x", tmp_path / "s", tmp_path / "pinned")


def test_the_readme_says_which_data_is_real():
    from hall_monitor.governance.controls import ROOT

    readme = (ROOT / "README.md").read_text()
    assert "All data in this repository is synthetic." not in readme
    assert "data/cfpb/" in readme


# Re-review of Review 15.


def test_every_pinned_snapshot_has_a_recorded_manifest_hash(tmp_path):
    names = {p.name for p in ingest.PINNED_SNAPSHOTS.iterdir() if p.is_dir()}
    assert names == set(ingest.PINNED_MANIFESTS)
    import shutil

    shutil.copytree(ingest.PINNED_SNAPSHOTS / ingest.PINNED_NAME, tmp_path / "unlisted")
    with pytest.raises(ingest.SnapshotError, match="no recorded manifest hash"):
        ingest.load_pinned("unlisted", tmp_path)


def test_a_bad_stored_cursor_fails_with_its_own_code(store, tmp_path):
    store.set_cursor("9999999999999999_1")
    gateway, _ = desk(FakeCfpb([record(1)]))
    summary = ingest.pull(store, gateway, snapshots=tmp_path)
    assert summary["status"] == "failed" and summary["error"] == "stored_cursor_invalid"
    assert ingest.pull(store, gateway, snapshots=tmp_path, since=date(2026, 10, 1))["new"] == 1


def test_a_page_with_no_usable_cursor_fails_with_its_own_code(store, tmp_path):
    hit = record(1)
    hit["sort"] = [1, "1"]
    gateway, _ = desk(FakeCfpb([hit]))
    summary = ingest.pull(store, gateway, snapshots=tmp_path, since=date(2026, 10, 1))
    assert summary["error"] == "no_usable_cursor" and summary["new"] == 1


def test_ids_of_different_lengths_in_one_millisecond_still_advance(store, tmp_path, monkeypatch):
    """If CFPB breaks ties by sorting IDs as text, "9000000" comes before "900001"."""
    first, second = record(1), record(1)
    first["_source"]["complaint_id"], first["sort"][1] = "9000000", "9000000"
    second["_source"]["complaint_id"], second["sort"][1] = "900001", "900001"

    class TextOrder(FakeCfpb):
        def __call__(self, request):
            self.requests.append(request)
            page = self.hits[: int(request.url.params["size"])]
            if request.url.params.get("search_after"):
                page = self.hits[1:2]
            if len(self.requests) > 2:
                page = []
            return httpx.Response(200, json={"hits": {"hits": page}})

    monkeypatch.setattr(ingest, "PAGE_SIZE", 1)
    gateway, _ = desk(TextOrder([first, second]))
    summary = ingest.pull(store, gateway, snapshots=tmp_path, since=date(2026, 10, 1), capacity=3)
    assert summary["status"] != "failed" and summary["new"] == 2
