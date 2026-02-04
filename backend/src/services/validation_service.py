"""Validation service for AI-Friendly API dry-run operations.

Provides validate_only (dry-run) functionality that validates operations
without making changes. Returns structured validation results including
would_affect metrics.
"""

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.exceptions import (
    AssetNotFoundError,
    ClipNotFoundError,
    InvalidTimeRangeError,
    LayerNotFoundError,
    MissingRequiredFieldError,
)
from src.models.asset import Asset
from src.models.project import Project
from src.schemas.ai import AddClipRequest, MoveClipRequest, UpdateClipTransformRequest


class WouldAffect:
    """Structured impact prediction for validate_only operations.

    All field names and units are fixed per AI-Friendly spec.
    """

    def __init__(
        self,
        clips_created: int = 0,
        clips_modified: int = 0,
        clips_deleted: int = 0,
        duration_change_ms: int = 0,
        layers_affected: list[str] | None = None,
    ):
        self.clips_created = clips_created
        self.clips_modified = clips_modified
        self.clips_deleted = clips_deleted
        self.duration_change_ms = duration_change_ms
        self.layers_affected = layers_affected or []

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API response."""
        return {
            "clips_created": self.clips_created,
            "clips_modified": self.clips_modified,
            "clips_deleted": self.clips_deleted,
            "duration_change_ms": self.duration_change_ms,
            "layers_affected": self.layers_affected,
        }


class ValidationResult:
    """Result of a validate_only operation."""

    def __init__(
        self,
        valid: bool,
        warnings: list[str] | None = None,
        would_affect: WouldAffect | None = None,
    ):
        self.valid = valid
        self.warnings = warnings or []
        self.would_affect = would_affect or WouldAffect()

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API response."""
        return {
            "valid": self.valid,
            "warnings": self.warnings,
            "would_affect": self.would_affect.to_dict(),
        }


class ValidationService:
    """Service for validate_only (dry-run) operations."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def validate_add_clip(
        self,
        project: Project,
        request: AddClipRequest,
    ) -> ValidationResult:
        """Validate clip creation without actually creating it.

        Args:
            project: The target project
            request: The clip creation request

        Returns:
            ValidationResult with valid flag, warnings, and would_affect metrics

        Raises:
            DougaError subclasses for validation failures
        """
        warnings: list[str] = []
        timeline = project.timeline_data or {}

        # Validate layer exists
        layer = self._find_layer_by_id(timeline, request.layer_id)
        if layer is None:
            raise LayerNotFoundError(request.layer_id)

        full_layer_id = layer.get("id", request.layer_id)

        # Validate timing
        if request.start_ms < 0:
            raise InvalidTimeRangeError(
                message="start_ms cannot be negative",
                start_ms=request.start_ms,
                end_ms=request.start_ms + request.duration_ms,
                field="start_ms",
            )

        if request.duration_ms <= 0:
            raise InvalidTimeRangeError(
                message="duration_ms must be positive",
                field="duration_ms",
            )

        # Validate in_point < out_point if both specified
        if request.out_point_ms is not None and request.in_point_ms >= request.out_point_ms:
            raise InvalidTimeRangeError(
                message="in_point_ms must be less than out_point_ms",
                start_ms=request.in_point_ms,
                end_ms=request.out_point_ms,
                field="in_point_ms",
            )

        # Validate asset if provided
        if request.asset_id:
            asset = await self._get_asset(str(request.asset_id))
            if asset is None:
                raise AssetNotFoundError(str(request.asset_id))

            # Validate timing against asset duration
            if asset.duration_ms:
                effective_out = (
                    request.out_point_ms
                    if request.out_point_ms is not None
                    else asset.duration_ms
                )

                if effective_out > asset.duration_ms:
                    raise InvalidTimeRangeError(
                        message=f"out_point_ms ({effective_out}) exceeds asset duration ({asset.duration_ms})",
                        field="out_point_ms",
                    )

                if request.in_point_ms >= effective_out:
                    raise InvalidTimeRangeError(
                        message=f"in_point_ms ({request.in_point_ms}) must be less than out_point_ms ({effective_out})",
                        field="in_point_ms",
                    )
        else:
            # Clips must have either asset_id OR text_content
            if not request.text_content:
                raise MissingRequiredFieldError(
                    "Clip must have either asset_id or text_content"
                )

        # Check for potential overlaps (warning only, not error)
        overlapping_clips = self._find_overlapping_clips(
            layer,
            request.start_ms,
            request.start_ms + request.duration_ms,
        )
        if overlapping_clips:
            clip_ids = ", ".join(c.get("id", "unknown") for c in overlapping_clips)
            warnings.append(f"Clip would overlap with: {clip_ids}")

        # Calculate would_affect
        current_duration = timeline.get("duration_ms", 0)
        new_end = request.start_ms + request.duration_ms
        duration_change = max(0, new_end - current_duration)

        would_affect = WouldAffect(
            clips_created=1,
            clips_modified=0,
            clips_deleted=0,
            duration_change_ms=duration_change,
            layers_affected=[full_layer_id],
        )

        return ValidationResult(
            valid=True,
            warnings=warnings,
            would_affect=would_affect,
        )

    def _find_layer_by_id(
        self, timeline: dict[str, Any], layer_id: str
    ) -> dict[str, Any] | None:
        """Find a layer by ID (supports partial matching).

        Matches ai_service._find_layer_by_id logic: stored ID must equal or
        start with the search ID (unidirectional prefix matching).
        """
        layers = timeline.get("layers", [])

        for layer in layers:
            lid = layer.get("id", "")
            # Match by full ID or partial ID (stored ID starts with search ID)
            if lid == layer_id or lid.startswith(layer_id):
                return layer

        return None

    def _find_overlapping_clips(
        self,
        layer: dict[str, Any],
        start_ms: int,
        end_ms: int,
    ) -> list[dict[str, Any]]:
        """Find clips that would overlap with the given time range."""
        overlapping = []
        clips = layer.get("clips", [])

        for clip in clips:
            clip_start = clip.get("start_ms", 0)
            clip_duration = clip.get("duration_ms", 0)
            clip_end = clip_start + clip_duration

            # Check for overlap
            if start_ms < clip_end and end_ms > clip_start:
                overlapping.append(clip)

        return overlapping

    async def _get_asset(self, asset_id: str) -> Asset | None:
        """Get asset by ID."""
        try:
            asset_uuid = UUID(asset_id)
        except ValueError:
            return None

        result = await self.db.execute(
            select(Asset).where(Asset.id == asset_uuid)
        )
        return result.scalar_one_or_none()

    def _find_clip_by_id(
        self, timeline: dict[str, Any], clip_id: str
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str | None]:
        """Find a clip by ID (supports partial matching).

        Matches ai_service._find_clip_by_id logic: stored ID must equal or
        start with the search ID (unidirectional prefix matching).

        Returns:
            Tuple of (clip_data, layer, full_clip_id) or (None, None, None) if not found.
        """
        layers = timeline.get("layers", [])

        for layer in layers:
            for clip in layer.get("clips", []):
                cid = clip.get("id", "")
                # Match by full ID or partial ID (stored ID starts with search ID)
                if cid == clip_id or cid.startswith(clip_id):
                    return clip, layer, cid

        return None, None, None

    async def validate_move_clip(
        self,
        project: Project,
        clip_id: str,
        request: MoveClipRequest,
    ) -> ValidationResult:
        """Validate clip move without actually moving it.

        Args:
            project: The target project
            clip_id: ID of the clip to move
            request: The move request

        Returns:
            ValidationResult with valid flag, warnings, and would_affect metrics

        Raises:
            ClipNotFoundError: If clip not found
            LayerNotFoundError: If target layer not found
            InvalidTimeRangeError: If new_start_ms is invalid
        """
        warnings: list[str] = []
        timeline = project.timeline_data or {}

        # Validate clip exists
        clip_data, source_layer, full_clip_id = self._find_clip_by_id(timeline, clip_id)
        if clip_data is None:
            raise ClipNotFoundError(clip_id)

        # Validate new_start_ms
        if request.new_start_ms < 0:
            raise InvalidTimeRangeError(
                message="new_start_ms cannot be negative",
                start_ms=request.new_start_ms,
                field="new_start_ms",
            )

        # Validate target layer if specified
        target_layer = source_layer
        full_target_layer_id = source_layer.get("id", "")
        if request.new_layer_id:
            target_layer = self._find_layer_by_id(timeline, request.new_layer_id)
            if target_layer is None:
                raise LayerNotFoundError(request.new_layer_id)
            full_target_layer_id = target_layer.get("id", request.new_layer_id)

        # Check for potential overlaps in target layer
        clip_duration = clip_data.get("duration_ms", 0)
        overlapping_clips = self._find_overlapping_clips(
            target_layer,
            request.new_start_ms,
            request.new_start_ms + clip_duration,
        )
        # Exclude self from overlap check
        overlapping_clips = [c for c in overlapping_clips if c.get("id") != full_clip_id]
        if overlapping_clips:
            clip_ids = ", ".join(c.get("id", "unknown") for c in overlapping_clips)
            warnings.append(f"Clip would overlap with: {clip_ids}")

        # Calculate would_affect
        layers_affected = [source_layer.get("id", "")]
        if target_layer != source_layer:
            layers_affected.append(full_target_layer_id)

        would_affect = WouldAffect(
            clips_created=0,
            clips_modified=1,
            clips_deleted=0,
            duration_change_ms=0,  # Move doesn't change timeline duration
            layers_affected=list(set(layers_affected)),  # Dedupe
        )

        return ValidationResult(
            valid=True,
            warnings=warnings,
            would_affect=would_affect,
        )

    async def validate_transform_clip(
        self,
        project: Project,
        clip_id: str,
        request: UpdateClipTransformRequest,
    ) -> ValidationResult:
        """Validate clip transform update without actually updating it.

        Args:
            project: The target project
            clip_id: ID of the clip to transform
            request: The transform request

        Returns:
            ValidationResult with valid flag, warnings, and would_affect metrics

        Raises:
            ClipNotFoundError: If clip not found
        """
        warnings: list[str] = []
        timeline = project.timeline_data or {}

        # Validate clip exists
        clip_data, layer, full_clip_id = self._find_clip_by_id(timeline, clip_id)
        if clip_data is None:
            raise ClipNotFoundError(clip_id)

        layer_id = layer.get("id", "") if layer else ""

        # Transform validation is minimal - just check clip exists
        # All transform values are already validated by Pydantic schema

        would_affect = WouldAffect(
            clips_created=0,
            clips_modified=1,
            clips_deleted=0,
            duration_change_ms=0,
            layers_affected=[layer_id] if layer_id else [],
        )

        return ValidationResult(
            valid=True,
            warnings=warnings,
            would_affect=would_affect,
        )

    async def validate_delete_clip(
        self,
        project: Project,
        clip_id: str,
    ) -> ValidationResult:
        """Validate clip deletion without actually deleting it.

        Args:
            project: The target project
            clip_id: ID of the clip to delete

        Returns:
            ValidationResult with valid flag, warnings, and would_affect metrics

        Raises:
            ClipNotFoundError: If clip not found
        """
        warnings: list[str] = []
        timeline = project.timeline_data or {}

        # Validate clip exists
        clip_data, layer, full_clip_id = self._find_clip_by_id(timeline, clip_id)
        if clip_data is None:
            raise ClipNotFoundError(clip_id)

        layer_id = layer.get("id", "") if layer else ""

        # Check if this is the last clip and might affect duration
        clip_end = clip_data.get("start_ms", 0) + clip_data.get("duration_ms", 0)
        current_duration = timeline.get("duration_ms", 0)

        duration_change = 0
        if clip_end >= current_duration:
            # This clip might be determining the timeline duration
            # Calculate new duration after deletion
            all_ends = []
            for l in timeline.get("layers", []):
                for c in l.get("clips", []):
                    if c.get("id") != full_clip_id:
                        all_ends.append(c.get("start_ms", 0) + c.get("duration_ms", 0))
            for t in timeline.get("audio_tracks", []):
                for c in t.get("clips", []):
                    all_ends.append(c.get("start_ms", 0) + c.get("duration_ms", 0))

            new_duration = max(all_ends) if all_ends else 0
            duration_change = new_duration - current_duration

        would_affect = WouldAffect(
            clips_created=0,
            clips_modified=0,
            clips_deleted=1,
            duration_change_ms=duration_change,
            layers_affected=[layer_id] if layer_id else [],
        )

        return ValidationResult(
            valid=True,
            warnings=warnings,
            would_affect=would_affect,
        )
