#!/usr/bin/env python3
"""Audit the live FastMCP tool surface: descriptions, schema drift, tool-count budget.

Guards against the "God Tool" and "vague/missing tool description" MCP
anti-patterns (arXiv:2606.30317) as the server evolves, and pins the schema so
any accidental drift (renamed args, leaked internal params, a new tool) shows
up as a reviewable diff instead of silently shipping.

Usage:
    python scripts/audit_mcp_surface.py                  # human-readable report
    python scripts/audit_mcp_surface.py --format json     # machine-readable report
    python scripts/audit_mcp_surface.py --update-snapshot # write the golden file

Exits non-zero when any finding is present (unless --update-snapshot is used).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

from mcp_server.server import mcp  # noqa: E402

SNAPSHOT_PATH = PROJECT_ROOT / "scripts" / "mcp_tool_surface_snapshot.json"

# arXiv:2606.30317 reports tool-selection accuracy degrading past ~10-15 tools
# for Haiku-class models and ~20-30 tools for Sonnet-class models.
TOOL_COUNT_SOFT_BUDGET = 15
TOOL_COUNT_HARD_BUDGET = 30

MIN_DESCRIPTION_LENGTH = 40

WHEN_TO_CALL_PHRASES = (
    "use when", "use this when", "use this instead", "use instead", "instead of",
    "prefer", "call this when", "call this every", "before you", "when the",
    "when you", "when a client", "if the", "if you", "typical flow", "poll ",
    "when fewer", "before asking", "before deciding",
)

# Internal/plumbing parameters that must never leak into the public schema
# (e.g. FastMCP's injected Context for progress/heartbeat reporting).
INTERNAL_ARG_NAMES = {"ctx", "context"}


async def get_live_tools():
    return await mcp.list_tools()


def build_snapshot(tools) -> dict[str, Any]:
    """Deterministic, comparable snapshot of the live tool surface."""
    snapshot = {}
    for tool in sorted(tools, key=lambda t: t.name):
        schema = tool.inputSchema or {}
        properties = schema.get("properties", {})
        snapshot[tool.name] = {
            "description": tool.description or "",
            "required": sorted(schema.get("required") or []),
            "parameters": {
                name: {
                    "type": prop.get("type"),
                    "default": prop.get("default", "<none>"),
                }
                for name, prop in sorted(properties.items())
            },
        }
    return snapshot


def diff_snapshot(live: dict[str, Any], golden: dict[str, Any]) -> list[dict[str, Any]]:
    findings = []
    live_names, golden_names = set(live), set(golden)

    for name in sorted(golden_names - live_names):
        findings.append({
            "tool": name,
            "check": "schema_snapshot",
            "detail": "Tool present in golden snapshot but missing from the live server.",
        })
    for name in sorted(live_names - golden_names):
        findings.append({
            "tool": name,
            "check": "schema_snapshot",
            "detail": "New tool not present in the golden snapshot — review and run --update-snapshot.",
        })
    for name in sorted(live_names & golden_names):
        if live[name] != golden[name]:
            findings.append({
                "tool": name,
                "check": "schema_snapshot",
                "detail": "Tool schema/description drifted from the golden snapshot — review and run --update-snapshot.",
            })
    return findings


def audit_tools(tools) -> list[dict[str, Any]]:
    """Return a list of finding dicts; an empty list means a clean audit."""
    findings: list[dict[str, Any]] = []

    count = len(tools)
    if count > TOOL_COUNT_HARD_BUDGET:
        findings.append({
            "tool": None,
            "check": "tool_count_hard_budget",
            "detail": f"{count} tools exceeds the hard budget of {TOOL_COUNT_HARD_BUDGET}.",
        })
    elif count > TOOL_COUNT_SOFT_BUDGET:
        findings.append({
            "tool": None,
            "check": "tool_count_soft_budget",
            "detail": (
                f"{count} tools exceeds the soft budget of {TOOL_COUNT_SOFT_BUDGET} "
                "(arXiv:2606.30317 Haiku-class risk band) — review before adding more."
            ),
        })

    for tool in tools:
        description = tool.description or ""
        schema = tool.inputSchema or {}
        properties = schema.get("properties", {})

        if len(description.strip()) < MIN_DESCRIPTION_LENGTH:
            findings.append({
                "tool": tool.name,
                "check": "description_present",
                "detail": f"Description is missing or shorter than {MIN_DESCRIPTION_LENGTH} chars.",
            })
            continue  # further shape checks on an empty description aren't meaningful

        lowered = description.lower()
        if not any(phrase in lowered for phrase in WHEN_TO_CALL_PHRASES):
            findings.append({
                "tool": tool.name,
                "check": "when_to_call",
                "detail": "Description explains what the tool does but not when to call it.",
            })

        for arg_name in properties:
            if arg_name in INTERNAL_ARG_NAMES:
                findings.append({
                    "tool": tool.name,
                    "check": "internal_arg_leak",
                    "detail": f"Argument '{arg_name}' looks like an internal/context param leaked into the public schema.",
                })
            elif arg_name not in description:
                findings.append({
                    "tool": tool.name,
                    "check": "arg_description_missing",
                    "detail": f"Argument '{arg_name}' is not documented anywhere in the docstring.",
                })

    return findings


def load_golden_snapshot() -> dict[str, Any]:
    if not SNAPSHOT_PATH.exists():
        return {}
    return json.loads(SNAPSHOT_PATH.read_text())


def write_golden_snapshot(snapshot: dict[str, Any]) -> None:
    SNAPSHOT_PATH.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n")


def run_audit() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Run the full audit; returns (findings, live_snapshot)."""
    tools = asyncio.run(get_live_tools())
    snapshot = build_snapshot(tools)
    findings = audit_tools(tools)
    findings.extend(diff_snapshot(snapshot, load_golden_snapshot()))
    return findings, snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--format", choices=["text", "json"], default="text")
    parser.add_argument(
        "--update-snapshot",
        action="store_true",
        help="Write the current live tool surface as the new golden snapshot.",
    )
    args = parser.parse_args()

    if args.update_snapshot:
        tools = asyncio.run(get_live_tools())
        write_golden_snapshot(build_snapshot(tools))
        print(f"Wrote golden snapshot for {len(tools)} tools to {SNAPSHOT_PATH}")
        return 0

    findings, snapshot = run_audit()

    if args.format == "json":
        print(json.dumps({"tool_count": len(snapshot), "findings": findings}, indent=2, sort_keys=True))
    else:
        print(f"MCP tool surface audit — {len(snapshot)} tools")
        if not findings:
            print("OK: no findings.")
        else:
            for finding in findings:
                scope = finding["tool"] or "<surface>"
                print(f"FAIL [{finding['check']}] {scope}: {finding['detail']}")

    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
