"""Reading the CFPB complaint API: one read-only tool, behind the governed tool gateway.

    pipeline -> ToolGateway -> MCP tool cfpb_complaint_search -> CfpbClient -> egress guard -> CFPB

The tool is the desk's only way to reach CFPB, and only the pipeline may call it (no agent is
offered it). CfpbClient (hall_monitor/egress.py, the one module allowed an HTTP client) makes
GET requests through the egress guard, which refuses anything else (HM-25), spacing and
retrying them.

Minimisation happens here, at the door: each record keeps only an allow-list of fields, so the
company name (and anything CFPB adds later) never enters the system; the ZIP code is cut to its
first three digits. The names of dropped fields are returned, never their values, so a change
in CFPB's schema shows up in the pull's record.

CFPB's API ignores `frm`: it pages with search_after, a cursor "<epoch ms>_<complaint id>" from
each hit's sort value. The tool sorts by date received, oldest first, so a pull resumes exactly
after the last record it processed.
"""

from __future__ import annotations

import re

from fastmcp import FastMCP

from hall_monitor.egress import CfpbClient
from hall_monitor.registry import DataClass, Registry
from hall_monitor.tools.gateway import ToolGateway

CFPB_SEARCH = "cfpb_complaint_search"

# A complaint is one consumer's account of their own finances, with a state and a ZIP prefix:
# published, but personal and financial all the same.
TOOL_DATA_CLASSES: dict[str, frozenset[DataClass]] = {
    CFPB_SEARCH: frozenset({DataClass.PII, DataClass.FINANCIAL})
}
PIPELINE_TOOLS = frozenset({CFPB_SEARCH})

# The simulated company is a consumer lender and bank: its desk takes complaints about the
# products such a company offers. Credit reporting (most of CFPB's volume) goes to consumer
# reporting agencies, so it isn't on this desk.
DESK_PRODUCTS = (
    "Checking or savings account",
    "Credit card",
    "Debt collection",
    "Debt or credit management",
    "Money transfer, virtual currency, or money service",
    "Mortgage",
    "Payday loan, title loan, personal loan, or advance loan",
    "Prepaid card",
    "Student loan",
    "Vehicle loan or lease",
)

# Fields kept from each record. Everything else is dropped before the record leaves the tool.
KEPT_FIELDS = frozenset(
    {
        "complaint_id",
        "date_received",
        "date_sent_to_company",
        "product",
        "sub_product",
        "issue",
        "sub_issue",
        "state",
        "zip_code",
        "tags",
        "submitted_via",
        "company_response",
        "company_public_response",
        "timely",
        "complaint_what_happened",
    }
)

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?)?$"
)
_CURSOR = re.compile(r"^\d{10,16}_\d{1,12}$")
# CFPB's database starts in 2011; a cursor outside 2011 to 2100 isn't a real receipt time.
CURSOR_MS_RANGE = (1_293_840_000_000, 4_102_444_800_000)
# A field name CFPB adds is reported by name only if it looks like one of its own.
_FIELD_NAME = re.compile(r"^[a-z][a-z_]{0,39}$")
UNRECOGNISED_FIELD = "unrecognised_field"
_ZIP = re.compile(r"^(\d{3})(?:\d{2}|XX)$")


def minimise_hit(hit: object) -> dict:
    """One hit as the desk receives it: the allow-listed fields of its source, the ZIP cut to
    three digits, its cursor, and the names (never values) of the fields dropped."""
    source = hit.get("_source") if isinstance(hit, dict) else None
    sort = hit.get("sort") if isinstance(hit, dict) else None
    cursor = None
    if isinstance(sort, list) and len(sort) == 2:
        cursor = f"{sort[0]}_{sort[1]}"
        valid = (
            _CURSOR.match(cursor)
            and CURSOR_MS_RANGE[0] <= int(cursor.split("_")[0]) <= (CURSOR_MS_RANGE[1])
        )
        cursor = cursor if valid else None
    if not isinstance(source, dict):
        return {"source": None, "cursor": cursor, "dropped_fields": []}
    kept = {k: v for k, v in source.items() if k in KEPT_FIELDS}
    zip_code = kept.get("zip_code")
    if zip_code is not None:
        match = _ZIP.match(str(zip_code))
        # A ZIP that isn't five digits (or CFPB's "123XX") is dropped, not guessed at.
        kept["zip_code"] = f"{match.group(1)}XX" if match else None
    dropped = sorted(
        {k if _FIELD_NAME.match(str(k)) else UNRECOGNISED_FIELD for k in source} - KEPT_FIELDS
    )
    return {"source": kept, "cursor": cursor, "dropped_fields": dropped}


def build_complaint_server(client: CfpbClient) -> FastMCP:
    mcp = FastMCP("hall-monitor-complaints")

    @mcp.tool(name=CFPB_SEARCH)
    def cfpb_complaint_search(
        date_received_min: str, search_after: str | None = None, size: int = 100
    ) -> dict:
        """Read one page of public CFPB complaints about the desk's products, received on or
        after date_received_min (YYYY-MM-DD), oldest first, resuming after the search_after
        cursor if given. Read-only: it sends nothing anywhere. Returns the total CFPB counts,
        each record minimised (no company, ZIP cut to three digits) with its cursor, and the
        state of CFPB's data."""
        if not _DATE.match(date_received_min):
            raise ValueError("date_received_min must be YYYY-MM-DD")
        if search_after is not None and not _CURSOR.match(search_after):
            raise ValueError("search_after must be <epoch ms>_<complaint id>")
        if not 1 <= size <= 100:
            raise ValueError("size must be 1 to 100")
        params: list[tuple[str, str | int]] = [
            ("size", size),
            ("sort", "created_date_asc"),
            ("no_aggs", "true"),
            ("date_received_min", date_received_min),
            *(("product", p) for p in DESK_PRODUCTS),
        ]
        if search_after:
            params.append(("search_after", search_after))
        data = client.search(params)
        meta = data.get("_meta") if isinstance(data.get("_meta"), dict) else {}
        total = data["hits"].get("total")
        total = total.get("value") if isinstance(total, dict) else None
        updated = meta.get("last_updated")
        # Only values of the expected shape pass: CFPB's metadata is data, and the pull's audit
        # entry carries it.
        return {
            "total": total if type(total) is int and total >= 0 else None,
            "hits": [minimise_hit(h) for h in data["hits"]["hits"]],
            "data_status": {
                "last_updated": updated
                if isinstance(updated, str) and _TIMESTAMP.match(updated)
                else None,
                "is_data_stale": meta.get("is_data_stale")
                if isinstance(meta.get("is_data_stale"), bool)
                else None,
                "has_data_issue": meta.get("has_data_issue")
                if isinstance(meta.get("has_data_issue"), bool)
                else None,
            },
        }

    return mcp


def complaint_gateway(registry: Registry, client: CfpbClient) -> ToolGateway:
    """The governed gateway for the desk: the CFPB tool, callable by the pipeline only."""
    return ToolGateway(
        registry,
        build_complaint_server(client),
        tool_data_classes=TOOL_DATA_CLASSES,
        pipeline_tools=PIPELINE_TOOLS,
    )
