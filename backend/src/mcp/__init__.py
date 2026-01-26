"""MCP (Model Context Protocol) Server for Douga Video Editor.

This module provides an MCP server that exposes the AI integration API
to AI assistants like Claude.

Requirements:
    pip install mcp[cli] httpx

Usage:
    # Run as standalone server
    python -m src.mcp.server

    # Or integrate with existing FastAPI app
    from src.mcp import mcp_server
"""

try:
    from src.mcp.server import mcp_server
except ImportError:
    mcp_server = None

__all__ = ["mcp_server"]
