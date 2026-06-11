"""Project management MCP tools."""

import json

from mcp.server.fastmcp import Context

from src import api_client


async def create_project(
    name: str,
    description: str = "",
    width: int = 1920,
    height: int = 1080,
) -> str:
    """プロジェクトを新規作成する。

    Args:
        name: プロジェクト名
        description: プロジェクトの説明
        width: 動画幅（デフォルト1920）
        height: 動画高さ（デフォルト1080）

    Returns:
        作成されたプロジェクト情報（JSON）
    """
    result = await api_client.create_project(name, description, width, height)
    return json.dumps(result, ensure_ascii=False, indent=2)


async def get_project_overview(
    project_id: str,
) -> str:
    """プロジェクトの概要情報を取得する。

    Args:
        project_id: プロジェクトID (UUID)

    Returns:
        プロジェクト概要（JSON）。タイムラインの状態、アセット数など。
    """
    project = await api_client.get_project(project_id)

    # Build a concise overview (L1 summary)
    timeline = project.get("timeline_data", {})
    layers = timeline.get("layers", [])
    audio_tracks = timeline.get("audio_tracks", [])

    total_clips = sum(len(l.get("clips", [])) for l in layers)
    total_audio_clips = sum(len(t.get("clips", [])) for t in audio_tracks)

    overview = {
        "id": project["id"],
        "name": project["name"],
        "description": project.get("description"),
        "status": project["status"],
        "width": project["width"],
        "height": project["height"],
        "fps": project["fps"],
        "duration_ms": project["duration_ms"],
        "has_video_brief": project.get("video_brief") is not None,
        "has_video_plan": project.get("video_plan") is not None,
        "video_plan_status": (project.get("video_plan") or {}).get("status"),
        "timeline_summary": {
            "total_video_clips": total_clips,
            "total_audio_clips": total_audio_clips,
            "layers_with_clips": sum(1 for l in layers if l.get("clips")),
        },
    }

    return json.dumps(overview, ensure_ascii=False, indent=2)
