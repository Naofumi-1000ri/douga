"""douga MCP Server — AI video production tools for Claude Code.

Provides MCP tools to create projects, upload assets, generate video plans,
apply plans to timelines, and trigger rendering via the douga backend API.
"""

from mcp.server.fastmcp import FastMCP

from src.tools.assets import (
    get_asset_catalog,
    reclassify_asset,
    scan_folder,
    upload_assets,
)
from src.tools.plan import apply_plan, generate_plan, get_plan, update_plan
from src.tools.preview import (
    get_event_points,
    sample_event_points,
    sample_frame,
    validate_composition,
)
from src.tools.project import create_project, get_project_overview
from src.tools.render import get_render_status, render_video
from src.tools.timeline import edit_timeline

# Create MCP server
mcp = FastMCP(
    "douga",
    description="AI動画制作ツール — Udemy講座動画のたたき台を自動生成",
)

# Register all tools
mcp.tool()(scan_folder)
mcp.tool()(create_project)
mcp.tool()(upload_assets)
mcp.tool()(reclassify_asset)
mcp.tool()(get_asset_catalog)
mcp.tool()(generate_plan)
mcp.tool()(get_plan)
mcp.tool()(update_plan)
mcp.tool()(apply_plan)
mcp.tool()(render_video)
mcp.tool()(get_render_status)
mcp.tool()(get_project_overview)
mcp.tool()(edit_timeline)

# Preview / Inspection tools
mcp.tool()(get_event_points)
mcp.tool()(sample_frame)
mcp.tool()(sample_event_points)
mcp.tool()(validate_composition)


if __name__ == "__main__":
    mcp.run()
