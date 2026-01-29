"""AI Video Service: generates VideoPlan from VideoBrief + AssetCatalog using OpenAI."""

import json
import logging

import httpx

from src.config import get_settings
from src.schemas.ai_video import (
    AssetCatalogResponse,
    VideoBrief,
    VideoPlan,
)

logger = logging.getLogger(__name__)

PLAN_SYSTEM_PROMPT = """あなたはUdemy講座動画のタイムラインを設計するビデオプロデューサーです。

## タスク
ビデオブリーフとアセットカタログから、VideoPlan JSONを生成してください。

## Udemy動画ベストプラクティス
- イントロ: 15秒以内、アバター全画面で挨拶
- 目次: スライド+アバター小、10秒程度
- 本編(content/demo): ナレーション基準でタイミング設定
  - スライド説明: slide_with_avatar レイアウト
  - 操作実演: screen_capture レイアウト
- アウトロ: 10秒、text_only レイアウト
- BGM: 全編通して流す、ナレーション時にducking
- SE: セクション切替時にチャイム音

## レイヤー配置ルール
| layout | Background(L1) | Content(L2) | Avatar(L3) | Effects(L4) | Text(L5) |
|--------|---------------|-------------|------------|-------------|----------|
| avatar_fullscreen | 背景動画/画像 | - | アバター(scale:1.0, x:0, y:0) | - | テロップ |
| slide_with_avatar | 背景色 | スライド(scale:1.0, x:0, y:0) | アバター(scale:0.3, x:700, y:350) | - | テロップ |
| screen_capture | - | 操作画面(scale:1.0, x:0, y:0) | アバター(scale:0.25, x:750, y:380) | - | テロップ |
| text_only | 背景動画/画像 | - | - | - | テキスト |
| image_fullscreen | - | 画像(scale:1.0, x:0, y:0) | - | - | テロップ |

## 制約
- asset_idはカタログの実際のIDのみ使用可
- clipのduration_msはアセットのduration_ms以内
- ナレーション音声がある場合、そのdurationがセクション長の基準
- 各セクションのstart_ms = 前セクションの(start_ms + duration_ms)
- 同一レイヤー/トラック内でクリップは重複不可
- element/audioのidは "elem_001", "aud_001" 形式の一意な文字列
- sectionのidは "sec_001" 形式の一意な文字列

## 出力
以下のJSON構造に準拠したVideoPlan JSONのみを出力してください（説明不要）:

{
  "version": "1.0",
  "total_duration_ms": <合計>,
  "status": "draft",
  "sections": [
    {
      "id": "sec_001",
      "type": "<section_type>",
      "title": "<タイトル>",
      "layout": "<layout_type>",
      "start_ms": <開始>,
      "duration_ms": <長さ>,
      "elements": [
        {
          "id": "elem_001",
          "layer": "<background|content|avatar|effects|text>",
          "asset_id": "<uuid or null>",
          "text_content": "<text or null>",
          "start_ms": <セクション内相対>,
          "duration_ms": <長さ>,
          "transform": { "x": 0, "y": 0, "scale": 1.0, "rotation": 0 },
          "effects": { "chroma_key": null, "fade_in_ms": 0, "fade_out_ms": 0 },
          "text_style": null
        }
      ],
      "audio": [
        {
          "id": "aud_001",
          "track": "<narration|bgm|se>",
          "asset_id": "<uuid>",
          "start_ms": <セクション内相対>,
          "duration_ms": <長さ>,
          "volume": 1.0,
          "fade_in_ms": 0,
          "fade_out_ms": 0
        }
      ]
    }
  ],
  "asset_assignments": {
    "background": "<uuid>",
    "avatar": "<uuid>",
    "bgm": "<uuid>"
  }
}
"""


async def generate_video_plan(
    brief: VideoBrief,
    catalog: AssetCatalogResponse,
) -> VideoPlan:
    """Generate a VideoPlan using OpenAI given a brief and asset catalog.

    Args:
        brief: The user's video brief with section descriptions
        catalog: The project's asset catalog

    Returns:
        Generated VideoPlan

    Raises:
        RuntimeError: If OpenAI API call fails or returns invalid JSON
    """
    settings = get_settings()

    if not settings.openai_api_key:
        raise RuntimeError("OpenAI API key not configured")

    user_message = f"""## ビデオブリーフ
{brief.model_dump_json(indent=2)}

## アセットカタログ
{catalog.model_dump_json(indent=2)}

上記のブリーフとアセットに基づいて、VideoPlan JSONを生成してください。"""

    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.openai_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "gpt-4o",
                "messages": [
                    {"role": "system", "content": PLAN_SYSTEM_PROMPT},
                    {"role": "user", "content": user_message},
                ],
                "temperature": 0.3,
                "response_format": {"type": "json_object"},
            },
        )

    if response.status_code != 200:
        logger.error(f"OpenAI API error: {response.status_code} {response.text}")
        raise RuntimeError(f"OpenAI API error: {response.status_code}")

    data = response.json()
    content = data["choices"][0]["message"]["content"]

    try:
        plan_dict = json.loads(content)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse OpenAI response as JSON: {e}\nContent: {content}")
        raise RuntimeError(f"Invalid JSON from OpenAI: {e}")

    plan = VideoPlan.model_validate(plan_dict)
    return plan
