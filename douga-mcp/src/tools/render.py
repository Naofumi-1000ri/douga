"""Video rendering MCP tools."""

import json

from src import api_client


async def render_video(
    project_id: str,
) -> str:
    """動画レンダリングを開始する。

    Args:
        project_id: プロジェクトID

    Returns:
        レンダリングジョブ情報（job_id, status）のJSON
    """
    result = await api_client.render_video(project_id)
    return json.dumps(result, ensure_ascii=False, indent=2)


async def get_render_status(
    project_id: str,
) -> str:
    """レンダリングの進捗状況を取得する。

    Args:
        project_id: プロジェクトID

    Returns:
        ステータス情報（status, progress%, download_url等）のJSON
    """
    result = await api_client.get_render_status(project_id)
    return json.dumps(result, ensure_ascii=False, indent=2)
