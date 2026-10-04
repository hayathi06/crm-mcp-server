"""End-to-end test: start the server as a subprocess and talk to it over MCP (stdio)."""

import json
import os
import sys

import anyio
from mcp.client import Client
from mcp.client.stdio import StdioServerParameters


def _params(tmp_path, read_only=False):
    env = {**os.environ, "CRM_MCP_DB": str(tmp_path / "crm.db"),
           "PYTHONPATH": os.path.join(os.path.dirname(__file__), "..", "src")}
    if read_only:
        env["CRM_MCP_READ_ONLY"] = "1"
    return StdioServerParameters(command=sys.executable, args=["-m", "crm_mcp"], env=env)


def _payload(result):
    if getattr(result, "structured_content", None):
        return result.structured_content
    return json.loads(result.content[0].text)


def test_full_session(tmp_path):
    async def run():
        async with Client(_params(tmp_path)) as c:
            tools = {t.name: t for t in (await c.list_tools()).tools}
            assert set(tools) == {"search_leads", "get_lead", "find_duplicates", "recommend_salesperson",
                                  "pipeline_summary", "create_lead", "update_lead_status", "add_note",
                                  "assign_lead"}
            assert tools["search_leads"].annotations.read_only_hint is True
            assert tools["create_lead"].annotations.read_only_hint is False

            summary = _payload(await c.call_tool("pipeline_summary", {}))
            assert summary["total_leads"] == 40

            lead = _payload(await c.call_tool("get_lead", {"lead_id": 1}))
            blocked = _payload(await c.call_tool("create_lead", {
                "first_name": "New", "last_name": "Person", "email": lead["email"].upper()}))
            assert blocked["created"] is False and blocked["possible_duplicates"]

            bad = await c.call_tool("update_lead_status", {"lead_id": 1, "status": "Maybe"})
            assert bad.is_error

            resources = {str(r.uri) for r in (await c.list_resources()).resources}
            assert {"crm://schema", "crm://audit-log"} <= resources
            prompts = {p.name for p in (await c.list_prompts()).prompts}
            assert "daily_triage" in prompts
    anyio.run(run)


def test_read_only_session(tmp_path):
    async def run():
        async with Client(_params(tmp_path, read_only=True)) as c:
            res = await c.call_tool("add_note", {"lead_id": 1, "note": "hello"})
            assert res.is_error
            assert "read-only" in res.content[0].text
    anyio.run(run)
