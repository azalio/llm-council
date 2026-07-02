#!/usr/bin/env python3
"""Quick test for MCP server tools."""

import asyncio
import sys
import os
import pytest

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp_server.server import (
    get_available_models,
    list_conversations,
)


@pytest.mark.asyncio
async def test_tools():
    """Test basic tool functionality."""
    print("=" * 60)
    print("MCP Server Tool Tests")
    print("=" * 60)

    # Test get_available_models
    print("\n1. Testing get_available_models()...")
    result = await get_available_models()
    print(result)

    # Test list_conversations
    print("\n2. Testing list_conversations()...")
    result = await list_conversations()
    print(result)

    print("\n" + "=" * 60)
    print("All tests completed!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(test_tools())
