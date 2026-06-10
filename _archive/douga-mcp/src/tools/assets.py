"""Asset management MCP tools: scan, upload, classify."""

import json
import mimetypes
import os
from pathlib import Path

from src import api_client


async def scan_folder(
    path: str,
) -> str:
    """ローカルフォルダを走査し、動画制作に使える素材ファイルの一覧を返す。

    Args:
        path: ローカルフォルダのパス

    Returns:
        ファイル一覧（名前、サイズ、MIME type）のJSON
    """
    folder = Path(path)
    if not folder.exists():
        return json.dumps({"error": f"Folder not found: {path}"})
    if not folder.is_dir():
        return json.dumps({"error": f"Not a directory: {path}"})

    # Supported extensions
    supported = {
        ".mp4", ".mov", ".avi", ".webm",  # video
        ".mp3", ".wav", ".aac", ".ogg", ".m4a",  # audio
        ".png", ".jpg", ".jpeg", ".gif", ".webp",  # image
    }

    files = []
    for entry in sorted(folder.iterdir()):
        if not entry.is_file():
            continue
        if entry.suffix.lower() not in supported:
            continue

        mime_type = mimetypes.guess_type(str(entry))[0] or "application/octet-stream"
        size_mb = round(entry.stat().st_size / (1024 * 1024), 2)

        files.append({
            "name": entry.name,
            "path": str(entry),
            "size_mb": size_mb,
            "mime_type": mime_type,
        })

    result = {
        "folder": str(folder),
        "total_files": len(files),
        "files": files,
    }
    return json.dumps(result, ensure_ascii=False, indent=2)


async def upload_assets(
    project_id: str,
    files: list[str],
) -> str:
    """ローカルファイルをプロジェクトにバッチアップロードし、自動分類する。

    Args:
        project_id: プロジェクトID (UUID)
        files: アップロードするローカルファイルパスのリスト

    Returns:
        アップロード結果（各ファイルのasset_id、分類結果）のJSON
    """
    # Validate files exist
    valid_paths = []
    errors = []
    for f in files:
        if os.path.isfile(f):
            valid_paths.append(f)
        else:
            errors.append({"file": f, "error": "File not found"})

    if not valid_paths:
        return json.dumps({
            "error": "No valid files to upload",
            "file_errors": errors,
        }, ensure_ascii=False)

    result = await api_client.batch_upload_assets(project_id, valid_paths)

    if errors:
        result["file_errors"] = errors

    return json.dumps(result, ensure_ascii=False, indent=2)


async def reclassify_asset(
    project_id: str,
    asset_id: str,
    type: str,
    subtype: str,
) -> str:
    """アセットの分類を手動で修正する。

    Args:
        project_id: プロジェクトID
        asset_id: アセットID
        type: 新しいタイプ (video, audio, image)
        subtype: 新しいサブタイプ (avatar, background, slide, narration, bgm, se, screen, effect, other)

    Returns:
        更新結果のJSON
    """
    result = await api_client.reclassify_asset(project_id, asset_id, type, subtype)
    return json.dumps(result, ensure_ascii=False, indent=2)


async def get_asset_catalog(
    project_id: str,
) -> str:
    """AI向けアセットカタログを取得する。プラン生成の入力に使う。

    Args:
        project_id: プロジェクトID

    Returns:
        全アセット一覧（ID、名前、種類、duration等）と統計情報のJSON
    """
    result = await api_client.get_asset_catalog(project_id)
    return json.dumps(result, ensure_ascii=False, indent=2)
