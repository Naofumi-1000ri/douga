"""Timeline Operations — tool dispatch layer extracted from ai_service.py (Issue #284).

Handles:
- _execute_tool_calls: routes tool-call dicts from any provider to AIService methods
- _execute_chat_operations_on_project: legacy operation-dict executor
- _execute_chat_operations: timeline-target aware wrapper

All methods receive an ``ai_service`` reference so they can call back into the
main AIService without circular dependency.  This module has no SQLAlchemy
session of its own; mutations go through the passed ai_service.
"""

from __future__ import annotations

import copy
import logging
from typing import TYPE_CHECKING, Any

from sqlalchemy.orm.attributes import flag_modified

from src.schemas.ai import (
    BatchClipOperation,
    ChatAction,
    SemanticOperation,
)

if TYPE_CHECKING:
    from src.models.project import Project
    from src.services.ai_service import AIService

logger = logging.getLogger(__name__)


async def execute_tool_calls(
    ai_service: AIService,
    project: Project,
    tool_calls: list[dict[str, Any]],
    *,
    timeline_target: Any | None = None,
) -> list[ChatAction]:
    """Dispatch tool calls returned by any provider to the appropriate executor.

    Each entry in ``tool_calls`` is ``{"name": str, "arguments": dict}``.
    """
    actions: list[ChatAction] = []
    for call in tool_calls:
        name = call["name"]
        args = call.get("arguments", {})
        try:
            if name == "execute_operations":
                ops = args.get("operations", [])
                batch_ops = [
                    BatchClipOperation(
                        operation=op.get("operation", ""),
                        clip_id=op.get("clip_id"),
                        clip_type=op.get("clip_type", "video"),
                        data=op.get("data", {}),
                    )
                    for op in ops
                ]
                if batch_ops:
                    # Reuse existing batch execution (handles timeline_target swap)
                    inner_ops = [
                        {
                            "type": "batch",
                            "operations": [
                                {
                                    "operation": bo.operation,
                                    "clip_id": bo.clip_id,
                                    "clip_type": bo.clip_type,
                                    "data": bo.data,
                                }
                                for bo in batch_ops
                            ],
                        }
                    ]
                    inner_actions = await execute_chat_operations(
                        ai_service, project, inner_ops, timeline_target=timeline_target
                    )
                    actions.extend(inner_actions)
                else:
                    actions.append(
                        ChatAction(
                            type="execute_operations",
                            description="操作リストが空です",
                            applied=False,
                        )
                    )
            elif name == "add_layer":
                added_layer = await ai_service.add_layer(
                    project,
                    name=args.get("name", "新しいレイヤー"),
                    layer_type=args.get("layer_type", "content"),
                    insert_at=args.get("insert_at"),
                )
                actions.append(
                    ChatAction(
                        type="add_layer",
                        description=(
                            f"レイヤー '{added_layer.name}' を追加しました (id={added_layer.id})"
                        ),
                        applied=True,
                    )
                )
            elif name == "update_layer":
                layer_id = args.get("layer_id")
                if not layer_id:
                    raise ValueError("layer_id required for update_layer")
                updated_layer = await ai_service.update_layer(
                    project,
                    layer_id=layer_id,
                    name=args.get("name"),
                    visible=args.get("visible"),
                    locked=args.get("locked"),
                )
                if updated_layer is None:
                    raise ValueError(f"Layer not found: {layer_id}")
                actions.append(
                    ChatAction(
                        type="update_layer",
                        description=(
                            f"レイヤー '{updated_layer.name}' を更新しました (id={updated_layer.id})"
                        ),
                        applied=True,
                    )
                )
            elif name == "reorder_layers":
                layer_ids = args.get("layer_ids", [])
                if not layer_ids:
                    raise ValueError("layer_ids required for reorder_layers")
                result_layers = await ai_service.reorder_layers(project, layer_ids=layer_ids)
                actions.append(
                    ChatAction(
                        type="reorder_layers",
                        description=f"{len(result_layers)} 個のレイヤーを並べ替えました",
                        applied=True,
                    )
                )
            elif name == "delete_layer":
                layer_id = args.get("layer_id")
                if not layer_id:
                    raise ValueError("layer_id required for delete_layer")
                inner_ops = [{"type": "delete_layer", "layer_id": layer_id}]
                inner_actions = await execute_chat_operations(
                    ai_service, project, inner_ops, timeline_target=timeline_target
                )
                actions.extend(inner_actions)
            elif name == "rename_layer":
                sem_op = SemanticOperation(
                    operation="rename_layer",
                    target_layer_id=args.get("layer_id"),
                    parameters={"name": args.get("name")},
                )
                sem_result = await ai_service.execute_semantic_operation(project, sem_op)
                actions.append(
                    ChatAction(
                        type="rename_layer",
                        description=(
                            ", ".join(sem_result.changes_made)
                            if sem_result.changes_made
                            else sem_result.error_message or "rename_layer"
                        ),
                        applied=sem_result.success,
                    )
                )
            elif name in ("snap_to_previous", "snap_to_next", "close_gap"):
                clip_id = args.get("clip_id")
                layer_id = args.get("layer_id")
                sem_op = SemanticOperation(
                    operation=name,
                    target_clip_id=clip_id,
                    target_layer_id=layer_id,
                )
                sem_result = await ai_service.execute_semantic_operation(project, sem_op)
                actions.append(
                    ChatAction(
                        type=name,
                        description=(
                            ", ".join(sem_result.changes_made)
                            if sem_result.changes_made
                            else sem_result.error_message or name
                        ),
                        applied=sem_result.success,
                    )
                )
            else:
                actions.append(
                    ChatAction(
                        type=name,
                        description=f"不明なツール: {name}",
                        applied=False,
                    )
                )
        except Exception as exc:
            logger.exception(f"Tool call failed: {name}")
            actions.append(
                ChatAction(
                    type=name,
                    description=f"実行エラー: {exc}",
                    applied=False,
                )
            )
    return actions


async def execute_chat_operations_on_project(
    ai_service: AIService,
    project: Project,
    operations: list[dict[str, Any]],
) -> list[ChatAction]:
    """Execute parsed operations from Claude's response."""
    actions: list[ChatAction] = []
    for op in operations:
        op_type = op.get("type", "")
        try:
            if op_type == "semantic":
                sem_op = SemanticOperation(
                    operation=op.get("operation", ""),
                    target_clip_id=op.get("target_clip_id"),
                    target_layer_id=op.get("target_layer_id"),
                    target_track_id=op.get("target_track_id"),
                    parameters=op.get("parameters", {}),
                )
                sem_result = await ai_service.execute_semantic_operation(project, sem_op)
                actions.append(
                    ChatAction(
                        type="semantic",
                        description=", ".join(sem_result.changes_made)
                        if sem_result.changes_made
                        else sem_result.error_message or op.get("operation", ""),
                        applied=sem_result.success,
                    )
                )
            elif op_type == "batch":
                batch_ops: list[BatchClipOperation] = []
                for batch_op in op.get("operations", []):
                    logger.info(f"[AI Batch] Preparing operation: {batch_op}")
                    batch_ops.append(
                        BatchClipOperation(
                            operation=batch_op.get("operation", ""),
                            clip_id=batch_op.get("clip_id"),
                            clip_type=batch_op.get("clip_type", "video"),
                            data=batch_op.get("data", {}),
                        )
                    )
                if batch_ops:
                    batch_result = await ai_service.execute_batch_operations(project, batch_ops)
                    logger.info(
                        f"[AI Batch] Result: success={batch_result.success}, {batch_result.successful_operations}/{batch_result.total_operations}"
                    )
                    if batch_result.errors:
                        logger.error(f"[AI Batch] Errors: {batch_result.errors}")
                    if batch_result.results:
                        logger.info(f"[AI Batch] Details: {batch_result.results}")
                    # Include error in description for debugging
                    if batch_result.errors:
                        desc = f"{batch_result.successful_operations}/{batch_result.total_operations} 操作完了: {batch_result.errors[0]}"
                    else:
                        desc = f"{batch_result.successful_operations}/{batch_result.total_operations} 操作完了"
                    actions.append(
                        ChatAction(
                            type="batch",
                            description=desc,
                            applied=batch_result.success,
                        )
                    )
            elif op_type == "add_layer":
                data = op.get("data", {})
                name = data.get("name", "新しいレイヤー")
                layer_type = data.get("layer_type", "content")
                insert_at = data.get("insert_at")
                added_layer = await ai_service.add_layer(
                    project,
                    name=name,
                    layer_type=layer_type,
                    insert_at=insert_at,
                )
                actions.append(
                    ChatAction(
                        type="add_layer",
                        description=(
                            f"レイヤー '{added_layer.name}' を追加しました (id={added_layer.id})"
                        ),
                        applied=True,
                    )
                )
            elif op_type == "update_layer":
                layer_id = op.get("layer_id")
                if not layer_id:
                    raise ValueError("layer_id required for update_layer operation")
                data = op.get("data", {})
                updated_layer = await ai_service.update_layer(
                    project,
                    layer_id=layer_id,
                    name=data.get("name"),
                    visible=data.get("visible"),
                    locked=data.get("locked"),
                )
                if updated_layer is None:
                    raise ValueError(f"Layer not found: {layer_id}")
                actions.append(
                    ChatAction(
                        type="update_layer",
                        description=(
                            f"レイヤー '{updated_layer.name}' を更新しました (id={updated_layer.id})"
                        ),
                        applied=True,
                    )
                )
            elif op_type == "reorder_layers":
                data = op.get("data", {})
                layer_ids = data.get("layer_ids", [])
                if not layer_ids:
                    raise ValueError("layer_ids required for reorder_layers operation")
                reordered_layers = await ai_service.reorder_layers(project, layer_ids=layer_ids)
                actions.append(
                    ChatAction(
                        type="reorder_layers",
                        description=f"{len(reordered_layers)} 個のレイヤーを並べ替えました",
                        applied=True,
                    )
                )
            elif op_type == "delete_layer":
                layer_id = op.get("layer_id")
                if not layer_id:
                    raise ValueError("layer_id required for delete_layer operation")
                timeline = project.timeline_data or {}
                layers = timeline.get("layers", [])
                layer_to_delete = next((lay for lay in layers if lay.get("id") == layer_id), None)
                if layer_to_delete is None:
                    raise ValueError(f"Layer not found: {layer_id}")
                layer_name = layer_to_delete.get("name", layer_id)
                timeline["layers"] = [lay for lay in layers if lay.get("id") != layer_id]
                project.timeline_data = timeline
                flag_modified(project, "timeline_data")
                await ai_service.db.flush()
                actions.append(
                    ChatAction(
                        type="delete_layer",
                        description=f"レイヤー '{layer_name}' を削除しました (id={layer_id})",
                        applied=True,
                    )
                )
            else:
                actions.append(
                    ChatAction(
                        type=op_type,
                        description=f"不明な操作タイプ: {op_type}",
                        applied=False,
                    )
                )
        except Exception as e:
            logger.exception(f"Failed to execute chat operation: {op_type}")
            actions.append(
                ChatAction(
                    type=op_type,
                    description=f"実行エラー: {str(e)}",
                    applied=False,
                )
            )
    return actions


async def execute_chat_operations(
    ai_service: AIService,
    project: Project,
    operations: list[dict[str, Any]],
    *,
    timeline_target: Any | None = None,
) -> list[ChatAction]:
    """Execute chat operations against the active timeline target."""
    if timeline_target is None or timeline_target is project:
        return await execute_chat_operations_on_project(ai_service, project, operations)

    original_timeline = project.timeline_data
    original_duration_ms = project.duration_ms
    target_timeline_before = copy.deepcopy(timeline_target.timeline_data or {})
    target_duration_before = getattr(timeline_target, "duration_ms", project.duration_ms)

    try:
        project.timeline_data = timeline_target.timeline_data or {}
        project.duration_ms = target_duration_before
        actions = await execute_chat_operations_on_project(ai_service, project, operations)
        timeline_changed = (
            project.timeline_data != target_timeline_before
            or project.duration_ms != target_duration_before
        )
        timeline_target.timeline_data = project.timeline_data
        timeline_target.duration_ms = project.duration_ms
        if timeline_changed:
            current_version = getattr(timeline_target, "version", None)
            if isinstance(current_version, int):
                timeline_target.version = current_version + 1
            flag_modified(timeline_target, "timeline_data")
        return actions
    finally:
        project.timeline_data = original_timeline
        project.duration_ms = original_duration_ms
