"""Preview and inspection MCP tools for AI-driven visual quality control.

Tools:
- get_event_points: Detect key moments in the timeline
- sample_frame: Render a single preview frame
- sample_event_points: Auto-detect + sample frames at key moments
- validate_composition: Check composition rules without rendering
"""

import json

from src import api_client


async def get_event_points(
    project_id: str,
    include_audio: bool = True,
    include_visual: bool = True,
    min_gap_ms: int = 500,
) -> str:
    """タイムラインのイベントポイント（重要な瞬間）を検出する。

    クリップ境界、音声開始、セクション切替、無音区間を自動検出。
    sample_frameと組み合わせて、重要な瞬間だけを視覚的に確認できる。

    Args:
        project_id: プロジェクトID
        include_audio: 音声イベントを含める（ナレーション開始、BGM、SE）
        include_visual: ビジュアルイベントを含める（クリップ境界、レイヤー変更）
        min_gap_ms: 検出する最小無音ギャップ（ミリ秒）

    Returns:
        検出されたイベントポイントのリスト（time_ms, event_type, description）のJSON
    """
    result = await api_client.get_event_points(
        project_id,
        include_audio=include_audio,
        include_visual=include_visual,
        min_gap_ms=min_gap_ms,
    )
    return json.dumps(result, ensure_ascii=False, indent=2)


async def sample_frame(
    project_id: str,
    time_ms: int,
    resolution: str = "640x360",
) -> str:
    """指定時刻のプレビューフレーム（静止画）をレンダリングする。

    低解像度JEPGを生成し、AIが構図・配置・テキスト可読性を判定できる。
    レスポンスにはBase64エンコードされた画像が含まれる。

    Args:
        project_id: プロジェクトID
        time_ms: サンプリングする時刻（ミリ秒）
        resolution: 出力解像度（例: "640x360", "320x180"）

    Returns:
        レンダリングされたフレーム（base64 JPEG）のJSON
    """
    result = await api_client.sample_frame(
        project_id,
        time_ms=time_ms,
        resolution=resolution,
    )
    return json.dumps(result, ensure_ascii=False, indent=2)


async def sample_event_points(
    project_id: str,
    max_samples: int = 10,
    resolution: str = "640x360",
    include_audio: bool = True,
    min_gap_ms: int = 500,
) -> str:
    """イベントポイントを自動検出し、各ポイントのプレビューフレームをレンダリングする。

    get_event_points + sample_frame を1回の呼び出しで実行。
    AIが一度にすべての重要な瞬間を視覚的に確認できる。

    Args:
        project_id: プロジェクトID
        max_samples: 最大サンプル数（デフォルト: 10）
        resolution: 出力解像度（例: "640x360"）
        include_audio: 音声イベントを含める
        min_gap_ms: 検出する最小無音ギャップ（ミリ秒）

    Returns:
        サンプリングされたイベントポイント（time_ms, event_type, description, frame_base64）のJSON
    """
    result = await api_client.sample_event_points(
        project_id,
        max_samples=max_samples,
        resolution=resolution,
        include_audio=include_audio,
        min_gap_ms=min_gap_ms,
    )
    return json.dumps(result, ensure_ascii=False, indent=2)


async def validate_composition(
    project_id: str,
    rules: list[str] | None = None,
) -> str:
    """タイムラインの構図ルールを検証する（レンダリング不要）。

    クリップ重複、はみ出し、アセット不足、セーフゾーン違反、
    音声・映像の同期問題などを自動チェック。

    Args:
        project_id: プロジェクトID
        rules: チェックするルールのリスト（省略時は全ルール実行）
            利用可能なルール:
            - overlapping_clips: クリップ重複
            - clip_bounds: タイムライン外はみ出し
            - missing_assets: アセット不足
            - safe_zone: セーフゾーン（画面端5%）違反
            - empty_layers: 空レイヤー
            - audio_sync: 音声映像同期
            - duration_consistency: 尺の整合性
            - text_readability: テキスト読みやすさ
            - layer_ordering: レイヤー順序
            - gap_detection: 映像ギャップ検出

    Returns:
        検証結果（is_valid, issues, errors, warnings）のJSON
    """
    result = await api_client.validate_composition(
        project_id,
        rules=rules,
    )
    return json.dumps(result, ensure_ascii=False, indent=2)
