"""Video plan generation and management MCP tools."""

import json

from src import api_client


async def generate_plan(
    project_id: str,
    brief: dict,
) -> str:
    """ビデオブリーフとアセットカタログからビデオプランを生成する（AI使用）。

    Args:
        project_id: プロジェクトID
        brief: ビデオブリーフ（VideoBrief形式のdict）
            例:
            {
                "title": "Unity入門 セクション3",
                "description": "スクリプト基礎の説明",
                "style": "tutorial",
                "target_duration_seconds": 300,
                "language": "ja",
                "sections": [
                    {
                        "type": "intro",
                        "title": "このセクションの内容",
                        "description": "概要説明",
                        "estimated_duration_seconds": 15,
                        "assets_hint": ["avatar", "background"]
                    }
                ],
                "preferences": {
                    "use_avatar": true,
                    "avatar_position": "bottom-right",
                    "bgm_style": "calm",
                    "include_intro": true,
                    "include_outro": true,
                    "chroma_key_avatar": true,
                    "text_style": "modern"
                }
            }

    Returns:
        生成されたVideoPlan JSON
    """
    result = await api_client.generate_plan(project_id, brief)
    return json.dumps(result, ensure_ascii=False, indent=2)


async def get_plan(
    project_id: str,
) -> str:
    """現在のビデオプランを取得する。

    Args:
        project_id: プロジェクトID

    Returns:
        VideoPlan JSON
    """
    result = await api_client.get_plan(project_id)
    return json.dumps(result, ensure_ascii=False, indent=2)


async def update_plan(
    project_id: str,
    plan: dict,
) -> str:
    """ビデオプランを更新する。

    Args:
        project_id: プロジェクトID
        plan: 更新するVideoPlan（dict形式）

    Returns:
        更新後のVideoPlan JSON
    """
    result = await api_client.update_plan(project_id, plan)
    return json.dumps(result, ensure_ascii=False, indent=2)


async def apply_plan(
    project_id: str,
) -> str:
    """ビデオプランをタイムラインに適用する（決定論的変換、AI不使用）。

    プランの各セクション・エレメントをタイムラインの5レイヤー+3オーディオトラックに変換。
    既存のtimeline_dataは上書きされる。

    Args:
        project_id: プロジェクトID

    Returns:
        適用結果（duration_ms、layers_populated、audio_clips_added）のJSON
    """
    result = await api_client.apply_plan(project_id)
    return json.dumps(result, ensure_ascii=False, indent=2)
