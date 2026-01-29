"""Timeline editing MCP tools for fine-tuning."""

import json

from src import api_client


async def edit_timeline(
    project_id: str,
    operations: list[dict],
) -> str:
    """タイムラインを直接編集する（微調整用）。

    apply_plan後の微調整に使用。各operationで個別のクリップやトラックを操作。

    Args:
        project_id: プロジェクトID
        operations: 操作のリスト。各operationは以下の形式:
            - {"op": "move_clip", "clip_id": "...", "layer": "...", "start_ms": 1000}
            - {"op": "resize_clip", "clip_id": "...", "layer": "...", "duration_ms": 5000}
            - {"op": "delete_clip", "clip_id": "...", "layer": "..."}
            - {"op": "update_transform", "clip_id": "...", "layer": "...", "transform": {...}}
            - {"op": "move_audio_clip", "clip_id": "...", "track": "...", "start_ms": 1000}
            - {"op": "update_volume", "clip_id": "...", "track": "...", "volume": 0.8}

    Returns:
        更新後のタイムライン概要のJSON
    """
    # Get current timeline
    project = await api_client.get_project(project_id)
    timeline = project.get("timeline_data", {})

    changes_made = 0

    for operation in operations:
        op = operation.get("op")
        clip_id = operation.get("clip_id")

        if op in ("move_clip", "resize_clip", "delete_clip", "update_transform"):
            layer_type = operation.get("layer")
            for layer in timeline.get("layers", []):
                if layer.get("type") != layer_type:
                    continue
                for i, clip in enumerate(layer.get("clips", [])):
                    if clip.get("id") != clip_id:
                        continue

                    if op == "move_clip":
                        clip["start_ms"] = operation["start_ms"]
                        changes_made += 1
                    elif op == "resize_clip":
                        clip["duration_ms"] = operation["duration_ms"]
                        changes_made += 1
                    elif op == "delete_clip":
                        layer["clips"].pop(i)
                        changes_made += 1
                    elif op == "update_transform":
                        clip["transform"].update(operation["transform"])
                        changes_made += 1
                    break

        elif op in ("move_audio_clip", "update_volume"):
            track_type = operation.get("track")
            for track in timeline.get("audio_tracks", []):
                if track.get("type") != track_type:
                    continue
                for clip in track.get("clips", []):
                    if clip.get("id") != clip_id:
                        continue

                    if op == "move_audio_clip":
                        clip["start_ms"] = operation["start_ms"]
                        changes_made += 1
                    elif op == "update_volume":
                        clip["volume"] = operation["volume"]
                        changes_made += 1
                    break

    if changes_made > 0:
        # Recalculate duration
        max_duration = 0
        for layer in timeline.get("layers", []):
            for clip in layer.get("clips", []):
                clip_end = clip.get("start_ms", 0) + clip.get("duration_ms", 0)
                max_duration = max(max_duration, clip_end)
        for track in timeline.get("audio_tracks", []):
            for clip in track.get("clips", []):
                clip_end = clip.get("start_ms", 0) + clip.get("duration_ms", 0)
                max_duration = max(max_duration, clip_end)
        timeline["duration_ms"] = max_duration

        # Save back
        await api_client.update_timeline(project_id, timeline)

    result = {
        "status": "ok",
        "changes_made": changes_made,
        "total_operations": len(operations),
        "duration_ms": timeline.get("duration_ms", 0),
    }
    return json.dumps(result, ensure_ascii=False, indent=2)
