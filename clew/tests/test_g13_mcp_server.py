#!/usr/bin/env python3
"""
G13 — MCP Server Mode — test suite.

Verifies:
  1. MCPServerMode lists available tools correctly.
  2. Read-only mode only exposes safe tools.
  3. Write mode exposes additional tools.
  4. Custom allowed_tools overrides default sets.
  5. tools/list handler returns MCP-formatted tool definitions.
  6. tools/call handler routes to ToolEngine.
  7. tools/call rejects disallowed tools.
  8. initialize handler returns correct capabilities.
  9. MCPServerMode.call_tool() programmatic API.
  10. MCPServerMode.status() returns server state.
  11. JSON-RPC message framing.
  12. MCP tool schemas are valid JSON Schema.

Run:
    python -m pytest clew/tests/test_g13_mcp_server.py -v
"""

from __future__ import annotations

import json
import os
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest


# ── Test isolation ──────────────────────────────────────────────────────

@pytest.fixture
def mcp_server():
    """Create a fresh MCPServerMode instance for each test."""
    from clew.mcp_server import MCPServerMode
    return MCPServerMode(workspace="/tmp/test_workspace", allow_writes=False)


@pytest.fixture
def mcp_server_writable():
    """Create a MCPServerMode with write access."""
    from clew.mcp_server import MCPServerMode
    return MCPServerMode(workspace="/tmp/test_workspace", allow_writes=True)


# ── 1. Available tools ──────────────────────────────────────────────────

def test_read_only_tools(mcp_server):
    tools = mcp_server._get_available_tools()
    # Should include read-only tools
    assert "read_file" in tools
    assert "search_project" in tools
    assert "grep" in tools
    assert "glob" in tools
    # Should NOT include write tools
    assert "write_file" not in tools
    assert "delete_file" not in tools
    assert "execute_command" not in tools


def test_write_mode_tools(mcp_server_writable):
    tools = mcp_server_writable._get_available_tools()
    # Should include both read-only and write tools
    assert "read_file" in tools
    assert "write_file" in tools
    assert "str_replace" in tools
    assert "delete_file" in tools


def test_custom_allowed_tools():
    from clew.mcp_server import MCPServerMode
    server = MCPServerMode(
        workspace="/tmp/test",
        allowed_tools=["read_file", "list_files"],
    )
    tools = server._get_available_tools()
    assert tools == ["read_file", "list_files"]


# ── 2. tools/list handler ───────────────────────────────────────────────

def test_tools_list_handler(mcp_server):
    result = mcp_server._handle_tools_list({})
    assert "tools" in result
    assert len(result["tools"]) > 0

    # Each tool should have name, description, inputSchema
    for tool in result["tools"]:
        assert "name" in tool
        assert "description" in tool
        assert "inputSchema" in tool
        assert tool["name"] in ["read_file", "list_files", "search_project", "grep",
                                 "glob", "file_info", "get_project_structure",
                                 "get_skill", "search_tools", "select_tools"]


# ── 3. tools/call handler ───────────────────────────────────────────────

def test_tools_call_disallowed_tool(mcp_server):
    result = mcp_server._handle_tools_call({"name": "write_file", "arguments": {"path": "x", "content": "y"}})
    assert result["isError"] is True
    assert "not available" in result["content"][0]["text"]


def test_tools_call_allowed_tool(mcp_server):
    """Test that an allowed tool is routed to the engine."""
    with patch.object(mcp_server, "_get_tool_engine") as mock_engine:
        mock_engine.return_value.execute.return_value = "file contents here"

        result = mcp_server._handle_tools_call({"name": "read_file", "arguments": {"path": "test.py"}})
        assert result["isError"] is False
        assert "file contents" in result["content"][0]["text"]


def test_tools_call_engine_error(mcp_server):
    """Test that an engine error is returned as isError."""
    with patch.object(mcp_server, "_get_tool_engine") as mock_engine:
        mock_engine.return_value.execute.side_effect = RuntimeError("Engine crashed")

        result = mcp_server._handle_tools_call({"name": "read_file", "arguments": {"path": "x"}})
        assert result["isError"] is True
        assert "Error" in result["content"][0]["text"]


# ── 4. initialize handler ───────────────────────────────────────────────

def test_initialize_handler(mcp_server):
    result = mcp_server._handle_initialize({"protocolVersion": "2024-11-05"})
    assert result["protocolVersion"] == "2024-11-05"
    assert "tools" in result["capabilities"]
    assert result["serverInfo"]["name"] == "clew-mcp-server"
    assert result["serverInfo"]["version"] == "2.0.3"


# ── 5. Programmatic API ────────────────────────────────────────────────

def test_list_tools_programmatic(mcp_server):
    tools = mcp_server.list_tools()
    assert len(tools) > 0
    for tool in tools:
        assert "name" in tool
        assert "description" in tool


def test_call_tool_programmatic(mcp_server):
    with patch.object(mcp_server, "_get_tool_engine") as mock_engine:
        mock_engine.return_value.execute.return_value = "result"
        result = mcp_server.call_tool("read_file", {"path": "test.py"})
        assert result["isError"] is False


# ── 6. Status ────────────────────────────────────────────────────────────

def test_status(mcp_server):
    status = mcp_server.status()
    assert status["name"] == "clew-mcp-server"
    assert status["workspace"] == "/tmp/test_workspace"
    assert status["allow_writes"] is False
    assert status["available_tools"] > 0


# ── 7. JSON-RPC messaging ───────────────────────────────────────────────

def test_send_response(mcp_server, capsys):
    mcp_server._send_response(1, {"tools": []})
    captured = capsys.readouterr()
    assert "Content-Length:" in captured.out
    body = json.loads(captured.out.split("\r\n\r\n", 1)[1])
    assert body["jsonrpc"] == "2.0"
    assert body["id"] == 1
    assert "tools" in body["result"]


def test_send_error(mcp_server, capsys):
    mcp_server._send_error(1, -32601, "Method not found")
    captured = capsys.readouterr()
    body = json.loads(captured.out.split("\r\n\r\n", 1)[1])
    assert body["error"]["code"] == -32601
    assert "Method not found" in body["error"]["message"]


# ── 8. MCP tool schemas validation ──────────────────────────────────────

def test_tool_schemas_are_valid():
    from clew.mcp_server import TOOL_SCHEMAS
    for name, schema in TOOL_SCHEMAS.items():
        assert "description" in schema, f"Tool {name} missing description"
        assert "inputSchema" in schema, f"Tool {name} missing inputSchema"
        ischema = schema["inputSchema"]
        assert ischema.get("type") == "object", f"Tool {name} inputSchema type must be 'object'"
        assert "properties" in ischema, f"Tool {name} inputSchema missing properties"


# ── 9. Read-only vs write tool sets ─────────────────────────────────────

def test_read_only_and_write_sets_dont_overlap():
    from clew.mcp_server import READ_ONLY_TOOLS, WRITE_TOOLS
    overlap = READ_ONLY_TOOLS & WRITE_TOOLS
    assert len(overlap) == 0, f"Tool sets overlap: {overlap}"


# ── 10. ping handler ────────────────────────────────────────────────────

def test_ping_handler(mcp_server):
    result = mcp_server._handle_ping({})
    assert result == {}


# ── 11. initialized notification ────────────────────────────────────────

def test_initialized_handler(mcp_server):
    # Should not raise
    mcp_server._handle_initialized({})
