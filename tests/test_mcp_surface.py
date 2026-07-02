"""MCP tool-surface audit: descriptions, schema drift, tool-count budget.

Guards the "God Tool" and "vague/missing tool description" anti-patterns
(arXiv:2606.30317) as scripts/audit_mcp_surface.py — this test just runs the
same audit against the live server plus a couple of synthetic-failure checks
so a regression here fails CI, not just a manual script run.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.audit_mcp_surface import (  # noqa: E402
    INTERNAL_ARG_NAMES,
    TOOL_COUNT_HARD_BUDGET,
    audit_tools,
    build_snapshot,
    diff_snapshot,
    get_live_tools,
    load_golden_snapshot,
)


class _FakeTool:
    def __init__(self, name, description, properties=None, required=None):
        self.name = name
        self.description = description
        self.inputSchema = {
            "properties": properties or {},
            "required": required or [],
        }


def test_live_tool_surface_passes_the_audit():
    tools = asyncio.run(get_live_tools())
    findings = audit_tools(tools)
    assert findings == []


def test_live_tool_surface_matches_committed_golden_snapshot():
    tools = asyncio.run(get_live_tools())
    live_snapshot = build_snapshot(tools)
    golden_snapshot = load_golden_snapshot()

    assert golden_snapshot, "no golden snapshot committed — run scripts/audit_mcp_surface.py --update-snapshot"
    assert diff_snapshot(live_snapshot, golden_snapshot) == []


def test_live_tool_surface_stays_within_hard_budget():
    tools = asyncio.run(get_live_tools())
    assert len(tools) <= TOOL_COUNT_HARD_BUDGET


def test_vague_description_fails_the_audit():
    tool = _FakeTool("do_thing", "Does the thing.")
    findings = audit_tools([tool])
    checks = {f["check"] for f in findings}
    assert "description_present" in checks


def test_missing_when_to_call_fails_the_audit():
    tool = _FakeTool(
        "do_thing",
        "Performs a moderately complicated multi-step operation on the backend "
        "system and returns a structured result object to the caller.",
    )
    findings = audit_tools([tool])
    checks = {f["check"] for f in findings}
    assert "when_to_call" in checks


def test_leaked_context_arg_fails_the_audit():
    tool = _FakeTool(
        "do_thing",
        "Use this when you need to do the thing right now, not later.",
        properties={"ctx": {"type": "object"}},
    )
    findings = audit_tools([tool])
    leaks = [f for f in findings if f["check"] == "internal_arg_leak"]
    assert len(leaks) == 1
    assert "ctx" in leaks[0]["detail"]


def test_undocumented_argument_fails_the_audit():
    tool = _FakeTool(
        "do_thing",
        "Use this when you need to do the thing right now, not later.",
        properties={"mystery_flag": {"type": "boolean"}},
    )
    findings = audit_tools([tool])
    checks = {f["check"] for f in findings}
    assert "arg_description_missing" in checks


def test_over_budget_tool_count_fails_the_audit():
    tools = [
        _FakeTool(
            f"tool_{i}",
            f"Use this when you need capability number {i} performed on demand.",
        )
        for i in range(TOOL_COUNT_HARD_BUDGET + 1)
    ]
    findings = audit_tools(tools)
    checks = {f["check"] for f in findings}
    assert "tool_count_hard_budget" in checks


def test_internal_arg_names_cover_fastmcp_context():
    assert "ctx" in INTERNAL_ARG_NAMES
