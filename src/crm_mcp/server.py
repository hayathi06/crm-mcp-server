"""MCP server exposing the demo CRM to AI assistants.

Run it with ``crm-mcp`` (or ``python -m crm_mcp``). It talks MCP over stdio,
so any MCP client, such as Claude Desktop, can connect to it.

Environment variables
---------------------
CRM_MCP_DB         Path to the SQLite file (default: ./demo_crm.db).
                   It is created and filled with fictional data on first run.
CRM_MCP_READ_ONLY  Set to 1 to block every write tool.
"""

from __future__ import annotations

import json
import os
from typing import Annotated, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from .crm import CRM, STATUSES

READ_ONLY = os.environ.get("CRM_MCP_READ_ONLY", "").strip() in {"1", "true", "yes"}
crm = CRM(os.environ.get("CRM_MCP_DB", "demo_crm.db"), read_only=READ_ONLY)

mcp = MCPServer(
    name="demo-crm",
    title="Demo CRM",
    version="1.0.0",
    instructions=(
        "A demo sales CRM with fictional leads. Before creating a lead, the server checks for "
        "duplicates and refuses obvious ones unless allow_duplicate is true; explain any matches to "
        "the user before overriding. Use recommend_salesperson before assign_lead. "
        "Lead statuses: " + ", ".join(STATUSES) + "."
    ),
)

def _run(fn, *args):
    """Call the CRM and turn expected problems into clear tool errors the AI can read."""
    try:
        return fn(*args)
    except (ValueError, PermissionError) as exc:
        raise ToolError(str(exc)) from exc


READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)

Status = Literal["New", "Contacted", "Qualified", "Quote Sent", "Won", "Lost"]
State = Literal["VIC", "NSW", "QLD", "WA", "SA", "TAS", "ACT", "NT"]
LeadId = Annotated[int, Field(description="The lead's numeric id", ge=1)]


# ---------------------------------------------------------------- read tools
@mcp.tool(annotations=READ)
def search_leads(
    query: Annotated[str | None, Field(description="Text to match in name, email, phone or postcode")] = None,
    status: Status | None = None,
    state: State | None = None,
    owner: Annotated[str | None, Field(description="Salesperson name, or 'unassigned'")] = None,
    limit: Annotated[int, Field(ge=1, le=100)] = 20,
) -> dict:
    """Search leads by text, status, state or owner. Newest first."""
    return _run(crm.search_leads, query, status, state, owner, limit)


@mcp.tool(annotations=READ)
def get_lead(lead_id: LeadId) -> dict:
    """Get one lead with its owner and notes."""
    return _run(crm.get_lead, lead_id)


@mcp.tool(annotations=READ)
def find_duplicates(lead_id: LeadId) -> dict:
    """Check an existing lead against every other lead and explain any likely duplicates."""
    return _run(crm.find_duplicates, lead_id)


@mcp.tool(annotations=READ)
def recommend_salesperson(lead_id: LeadId) -> dict:
    """Rank salespeople for a lead by territory coverage and current workload."""
    return _run(crm.recommend_salesperson, lead_id)


@mcp.tool(annotations=READ)
def pipeline_summary(state: State | None = None) -> dict:
    """Lead counts by status and source, unassigned open leads, and win rate."""
    return _run(crm.pipeline_summary, state)


# --------------------------------------------------------------- write tools
@mcp.tool(annotations=WRITE)
def create_lead(
    first_name: str,
    last_name: str,
    email: str | None = None,
    phone: str | None = None,
    postcode: Annotated[str | None, Field(description="4-digit Australian postcode")] = None,
    state: State | None = None,
    source: Annotated[str | None, Field(description="e.g. Website form, Email enquiry, Phone, Event, Referral")] = None,
    interest: str | None = None,
    allow_duplicate: Annotated[bool, Field(description="Create even if a high-confidence duplicate exists")] = False,
) -> dict:
    """Create a lead. Runs duplicate detection first and refuses high-confidence duplicates
    unless allow_duplicate is true."""
    return _run(crm.create_lead, first_name, last_name, email, phone, postcode, state, source, interest, allow_duplicate)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=True, openWorldHint=False))
def update_lead_status(lead_id: LeadId, status: Status) -> dict:
    """Move a lead to a new pipeline status."""
    return _run(crm.update_status, lead_id, status)


@mcp.tool(annotations=WRITE)
def add_note(lead_id: LeadId, note: Annotated[str, Field(max_length=2000)]) -> dict:
    """Add a note to a lead."""
    return _run(crm.add_note, lead_id, note)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=True, openWorldHint=False))
def assign_lead(lead_id: LeadId, salesperson_id: Annotated[int, Field(ge=1)]) -> dict:
    """Assign a lead to a salesperson (use recommend_salesperson first)."""
    return _run(crm.assign_lead, lead_id, salesperson_id)


# ----------------------------------------------------- resources and prompts
@mcp.resource("crm://schema", name="CRM schema", mime_type="application/json")
def schema() -> str:
    """Field names, allowed statuses and how duplicate detection works."""
    return json.dumps({
        "lead_fields": ["id", "first_name", "last_name", "email", "phone", "postcode", "state",
                        "source", "interest", "status", "owner", "created_at", "updated_at"],
        "statuses": STATUSES,
        "duplicate_rules": {
            "high": ["same email (case-insensitive)", "same phone (formatting ignored)",
                     "same full name and postcode"],
            "medium": ["very similar name (>=85%) in the same postcode"],
            "low": ["same full name in the same state"],
        },
        "read_only": READ_ONLY,
    }, indent=2)


@mcp.resource("crm://audit-log", name="Recent changes", mime_type="application/json")
def audit_log() -> str:
    """The 20 most recent changes made through this server."""
    return json.dumps(crm.recent_activity(20), indent=2)


@mcp.prompt(title="Daily lead triage")
def daily_triage(state: str = "") -> str:
    """Walk through new and unassigned leads and suggest next actions."""
    scope = f" in {state.upper()}" if state else ""
    return (
        f"Review today's pipeline{scope}. 1) Call pipeline_summary. 2) Search for leads with status "
        f"'New'{scope}. 3) For each, run find_duplicates and flag likely duplicates. 4) For genuine "
        f"leads without an owner, call recommend_salesperson and propose an assignment. "
        f"Present a short table and ask me to confirm before changing anything."
    )


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
