"""The governed MCP server: the only way agents read policy records and claims history.

Run it standalone over stdio for inspection or another client:

    uv run python -m hall_monitor.tools.server

Inside the pipeline it runs in-process, behind hall_monitor.tools.gateway, which checks each
agent's allow-list before any call reaches it.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastmcp import FastMCP

from hall_monitor.data import DATA_DIR, load_policy_records

POLICY_LOOKUP = "policy_lookup"
CLAIMS_HISTORY = "claims_history"


def build_server(data_dir: Path = DATA_DIR) -> FastMCP:
    policies = load_policy_records(data_dir)
    history = json.loads((data_dir / "claims_history.json").read_text())
    mcp = FastMCP("hall-monitor-records")

    @mcp.tool(name=POLICY_LOOKUP)
    def policy_lookup(policy_number: str) -> dict:
        """Look up a travel insurance policy by its number (e.g. WTP-ESS-100231).

        Returns the plan, the insured person's name, the purchase date, the coverage period and
        the insured trip (destination, departure, return and initial deposit dates), or
        found=false if no policy has that number.
        """
        record = policies.get(policy_number.strip().upper())
        if record is None:
            return {"found": False, "policy_number": policy_number}
        return {"found": True, **record.model_dump(mode="json")}

    @mcp.tool(name=CLAIMS_HISTORY)
    def claims_history(policy_number: str) -> dict:
        """List the insured person's previous claims, on this or any earlier policy.

        Takes the current policy number and returns each earlier claim's reference, type,
        incident date, summary, items (with amounts and receipt references) and outcome. An
        empty list means no previous claims.
        """
        record = policies.get(policy_number.strip().upper())
        if record is None:
            return {"found": False, "policy_number": policy_number, "claims": []}
        claims = [c for c in history if c["insured_name"] == record.insured_name]
        return {"found": True, "insured_name": record.insured_name, "claims": claims}

    return mcp


if __name__ == "__main__":
    build_server().run()
