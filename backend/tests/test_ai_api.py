"""
Tests for AI Integration API endpoints.

These tests use in-memory fixtures and don't require external test data.
Run with: pytest tests/test_ai_api.py -v
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from src.schemas.ai import (
    AddAudioClipRequest,
    AddClipRequest,
    L1ProjectOverview,
    L2TimelineStructure,
    L3ClipDetails,
    MoveClipRequest,
    SemanticOperation,
    UpdateClipEffectsRequest,
    UpdateClipTransformRequest,
)
from src.services.ai_service import AIService


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def sample_timeline_data():
    """Sample timeline data for testing."""
    # Use valid UUIDs for asset_id fields
    asset_bg_id = str(uuid.uuid4())
    asset_avatar_id = str(uuid.uuid4())
    asset_narration_id = str(uuid.uuid4())
    asset_bgm_id = str(uuid.uuid4())

    return {
        "duration_ms": 120000,
        "layers": [
            {
                "id": "layer-background",
                "name": "Background",
                "type": "background",
                "visible": True,
                "locked": False,
                "clips": [
                    {
                        "id": "clip-bg-1",
                        "asset_id": asset_bg_id,
                        "start_ms": 0,
                        "duration_ms": 120000,
                        "in_point_ms": 0,
                        "out_point_ms": 120000,
                        "transform": {"x": 0, "y": 0, "scale": 1.0},
                        "effects": {"opacity": 1.0, "blend_mode": "normal"},
                    }
                ],
            },
            {
                "id": "layer-avatar",
                "name": "Avatar",
                "type": "avatar",
                "visible": True,
                "locked": False,
                "clips": [
                    {
                        "id": "clip-avatar-1",
                        "asset_id": asset_avatar_id,
                        "start_ms": 0,
                        "duration_ms": 30000,
                        "in_point_ms": 0,
                        "out_point_ms": 30000,
                        "transform": {"x": 400, "y": -200, "scale": 0.5},
                        "effects": {
                            "opacity": 1.0,
                            "blend_mode": "normal",
                            "chroma_key": {"enabled": True, "color": "#00FF00"},
                        },
                    },
                    {
                        "id": "clip-avatar-2",
                        "asset_id": asset_avatar_id,
                        "start_ms": 60000,
                        "duration_ms": 30000,
                        "in_point_ms": 0,
                        "out_point_ms": 30000,
                        "transform": {"x": 400, "y": -200, "scale": 0.5},
                        "effects": {"opacity": 1.0, "blend_mode": "normal"},
                    },
                ],
            },
            {
                "id": "layer-content",
                "name": "Content",
                "type": "content",
                "visible": True,
                "locked": False,
                "clips": [],
            },
        ],
        "audio_tracks": [
            {
                "id": "track-narration",
                "name": "Narration",
                "type": "narration",
                "volume": 1.0,
                "muted": False,
                "clips": [
                    {
                        "id": "clip-narration-1",
                        "asset_id": asset_narration_id,
                        "start_ms": 0,
                        "duration_ms": 90000,
                        "in_point_ms": 0,
                        "volume": 1.0,
                    }
                ],
            },
            {
                "id": "track-bgm",
                "name": "BGM",
                "type": "bgm",
                "volume": 0.3,
                "muted": False,
                "clips": [
                    {
                        "id": "clip-bgm-1",
                        "asset_id": asset_bgm_id,
                        "start_ms": 0,
                        "duration_ms": 120000,
                        "in_point_ms": 0,
                        "volume": 0.3,
                    }
                ],
            },
        ],
    }


@pytest.fixture
def mock_project(sample_timeline_data):
    """Mock Project object for testing."""
    project = MagicMock()
    project.id = uuid.uuid4()
    project.name = "Test Project"
    project.duration_ms = 120000
    project.width = 1920
    project.height = 1080
    project.fps = 30
    project.status = "draft"
    project.updated_at = datetime.now(timezone.utc)
    project.timeline_data = sample_timeline_data
    return project


@pytest.fixture
def mock_db():
    """Mock database session."""
    db = AsyncMock()
    db.flush = AsyncMock()
    return db


@pytest.fixture
def ai_service(mock_db):
    """AI Service instance with mock database."""
    return AIService(mock_db)


# =============================================================================
# Schema Validation Tests
# =============================================================================


class TestAddClipRequestValidation:
    """Tests for AddClipRequest schema validation."""

    def test_valid_request(self):
        """Valid request should pass validation."""
        request = AddClipRequest(
            layer_id="layer-uuid",
            asset_id=uuid.uuid4(),
            start_ms=0,
            duration_ms=5000,
        )
        assert request.start_ms == 0
        assert request.duration_ms == 5000

    def test_negative_start_ms_rejected(self):
        """Negative start_ms should be rejected."""
        with pytest.raises(ValidationError) as exc_info:
            AddClipRequest(
                layer_id="layer-uuid",
                start_ms=-1000,
                duration_ms=5000,
            )
        assert "start_ms" in str(exc_info.value)

    def test_zero_duration_rejected(self):
        """Zero duration should be rejected."""
        with pytest.raises(ValidationError) as exc_info:
            AddClipRequest(
                layer_id="layer-uuid",
                start_ms=0,
                duration_ms=0,
            )
        assert "duration_ms" in str(exc_info.value)

    def test_excessive_duration_rejected(self):
        """Duration exceeding 1 hour should be rejected."""
        with pytest.raises(ValidationError) as exc_info:
            AddClipRequest(
                layer_id="layer-uuid",
                start_ms=0,
                duration_ms=3700000,  # > 1 hour
            )
        assert "duration_ms" in str(exc_info.value)

    def test_transform_constraints(self):
        """Transform values should respect constraints."""
        with pytest.raises(ValidationError):
            AddClipRequest(
                layer_id="layer-uuid",
                start_ms=0,
                duration_ms=5000,
                scale=-0.5,  # Below minimum
            )


class TestUpdateClipTransformValidation:
    """Tests for UpdateClipTransformRequest validation."""

    def test_valid_transform(self):
        """Valid transform values should pass."""
        request = UpdateClipTransformRequest(
            x=100,
            y=-50,
            scale=1.5,
            rotation=45,
        )
        assert request.x == 100
        assert request.scale == 1.5

    def test_scale_out_of_range_rejected(self):
        """Scale values outside range should be rejected."""
        with pytest.raises(ValidationError):
            UpdateClipTransformRequest(scale=15.0)  # > 10.0

        with pytest.raises(ValidationError):
            UpdateClipTransformRequest(scale=0.001)  # < 0.01

    def test_rotation_constraints(self):
        """Rotation values should be within -360 to 360."""
        with pytest.raises(ValidationError):
            UpdateClipTransformRequest(rotation=400)


class TestSemanticOperationValidation:
    """Tests for SemanticOperation validation."""

    def test_valid_operations(self):
        """Valid operation types should be accepted."""
        for op in ["snap_to_previous", "snap_to_next", "close_gap", "auto_duck_bgm"]:
            request = SemanticOperation(operation=op)
            assert request.operation == op

    def test_invalid_operation_rejected(self):
        """Invalid operation types should be rejected."""
        with pytest.raises(ValidationError) as exc_info:
            SemanticOperation(operation="invalid_operation")
        assert "operation" in str(exc_info.value)

    def test_removed_operations_rejected(self):
        """Previously defined but removed operations should be rejected."""
        with pytest.raises(ValidationError):
            SemanticOperation(operation="select_forward")

        with pytest.raises(ValidationError):
            SemanticOperation(operation="apply_template")


class TestAddAudioClipValidation:
    """Tests for AddAudioClipRequest validation."""

    def test_valid_audio_request(self):
        """Valid audio clip request should pass."""
        request = AddAudioClipRequest(
            track_id="track-uuid",
            asset_id=uuid.uuid4(),
            start_ms=0,
            duration_ms=60000,
            volume=0.8,
        )
        assert request.volume == 0.8

    def test_volume_out_of_range_rejected(self):
        """Volume outside 0.0-2.0 should be rejected."""
        with pytest.raises(ValidationError):
            AddAudioClipRequest(
                track_id="track-uuid",
                asset_id=uuid.uuid4(),
                start_ms=0,
                duration_ms=60000,
                volume=3.0,
            )


# =============================================================================
# L1 Endpoint Tests
# =============================================================================


class TestGetProjectOverview:
    """Tests for L1 project overview endpoint."""

    @pytest.mark.asyncio
    async def test_returns_project_summary(self, ai_service, mock_project):
        """Should return correct project summary."""
        result = await ai_service.get_project_overview(mock_project)

        assert isinstance(result, L1ProjectOverview)
        assert result.project.name == "Test Project"
        assert result.project.dimensions == "1920x1080"
        assert result.project.fps == 30

    @pytest.mark.asyncio
    async def test_counts_clips_correctly(self, ai_service, mock_project):
        """Should count video and audio clips correctly."""
        result = await ai_service.get_project_overview(mock_project)

        # 2 avatar clips + 1 background clip = 3 video clips
        assert result.summary.total_video_clips == 3
        # 1 narration clip + 1 bgm clip = 2 audio clips
        assert result.summary.total_audio_clips == 2

    @pytest.mark.asyncio
    async def test_handles_empty_timeline(self, ai_service, mock_project):
        """Should handle empty timeline gracefully."""
        mock_project.timeline_data = {}
        result = await ai_service.get_project_overview(mock_project)

        assert result.summary.layer_count == 0
        assert result.summary.total_video_clips == 0


# =============================================================================
# L2 Endpoint Tests
# =============================================================================


class TestGetTimelineStructure:
    """Tests for L2 timeline structure endpoint."""

    @pytest.mark.asyncio
    async def test_returns_layer_summaries(self, ai_service, mock_project):
        """Should return summaries for all layers."""
        result = await ai_service.get_timeline_structure(mock_project)

        assert isinstance(result, L2TimelineStructure)
        assert len(result.layers) == 3
        assert result.layers[0].id == "layer-background"

    @pytest.mark.asyncio
    async def test_calculates_time_coverage(self, ai_service, mock_project):
        """Should calculate time coverage correctly."""
        result = await ai_service.get_timeline_structure(mock_project)

        # Avatar layer has gap between 30000 and 60000
        avatar_layer = next(l for l in result.layers if l.id == "layer-avatar")
        assert avatar_layer.clip_count == 2
        # Should have 2 separate time ranges due to gap
        assert len(avatar_layer.time_coverage) == 2


# =============================================================================
# L3 Endpoint Tests
# =============================================================================


class TestGetClipDetails:
    """Tests for L3 clip details endpoint."""

    @pytest.mark.asyncio
    async def test_returns_clip_details(self, ai_service, mock_project, mock_db):
        """Should return full clip details."""
        mock_db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: None))

        result = await ai_service.get_clip_details(mock_project, "clip-avatar-1")

        assert isinstance(result, L3ClipDetails)
        assert result.id == "clip-avatar-1"
        assert result.layer_id == "layer-avatar"
        assert result.timing.start_ms == 0
        assert result.timing.duration_ms == 30000

    @pytest.mark.asyncio
    async def test_includes_neighbor_context(self, ai_service, mock_project, mock_db):
        """Should include previous and next clip info."""
        mock_db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: None))

        result = await ai_service.get_clip_details(mock_project, "clip-avatar-1")

        # First clip has no previous
        assert result.previous_clip is None
        # Should have next clip info with gap
        assert result.next_clip is not None
        assert result.next_clip.id == "clip-avatar-2"
        assert result.next_clip.gap_ms == 30000  # Gap between 30000 and 60000

    @pytest.mark.asyncio
    async def test_returns_none_for_missing_clip(self, ai_service, mock_project):
        """Should return None for non-existent clip."""
        result = await ai_service.get_clip_details(mock_project, "nonexistent-clip")
        assert result is None


# =============================================================================
# Write Operation Tests
# =============================================================================


class TestAddClip:
    """Tests for add clip operation."""

    @pytest.mark.asyncio
    async def test_adds_clip_successfully(self, ai_service, mock_project, mock_db):
        """Should add a new clip successfully."""
        mock_db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: None))

        request = AddClipRequest(
            layer_id="layer-content",
            start_ms=0,
            duration_ms=10000,
        )

        result = await ai_service.add_clip(mock_project, request)

        assert result is not None
        assert result.timing.start_ms == 0
        assert result.timing.duration_ms == 10000

    @pytest.mark.asyncio
    async def test_rejects_overlap(self, ai_service, mock_project, mock_db):
        """Should reject clips that would overlap."""
        request = AddClipRequest(
            layer_id="layer-avatar",
            start_ms=15000,  # Overlaps with clip-avatar-1 (0-30000)
            duration_ms=10000,
        )

        with pytest.raises(ValueError) as exc_info:
            await ai_service.add_clip(mock_project, request)
        assert "overlap" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_rejects_invalid_layer(self, ai_service, mock_project):
        """Should reject clips targeting non-existent layers."""
        request = AddClipRequest(
            layer_id="nonexistent-layer",
            start_ms=0,
            duration_ms=10000,
        )

        with pytest.raises(ValueError) as exc_info:
            await ai_service.add_clip(mock_project, request)
        assert "Layer not found" in str(exc_info.value)


class TestMoveClip:
    """Tests for move clip operation."""

    @pytest.mark.asyncio
    async def test_moves_clip_successfully(self, ai_service, mock_project, mock_db):
        """Should move clip to new position."""
        mock_db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: None))

        # Move clip-avatar-2 to a non-overlapping position (after gap)
        # clip-avatar-2 is at 60000-90000, move it to 100000-130000
        request = MoveClipRequest(new_start_ms=100000)

        result = await ai_service.move_clip(mock_project, "clip-avatar-2", request)

        assert result is not None
        assert result.timing.start_ms == 100000

    @pytest.mark.asyncio
    async def test_rejects_overlap_on_move(self, ai_service, mock_project, mock_db):
        """Should reject moves that would cause overlap."""
        request = MoveClipRequest(new_start_ms=50000)  # Would overlap with clip-avatar-2

        with pytest.raises(ValueError) as exc_info:
            await ai_service.move_clip(mock_project, "clip-avatar-1", request)
        assert "overlap" in str(exc_info.value).lower()


class TestDeleteClip:
    """Tests for delete clip operation."""

    @pytest.mark.asyncio
    async def test_deletes_clip_successfully(self, ai_service, mock_project, mock_db):
        """Should delete existing clip."""
        result = await ai_service.delete_clip(mock_project, "clip-avatar-1")
        assert result is True

        # Verify clip is removed
        avatar_layer = next(
            l for l in mock_project.timeline_data["layers"] if l["id"] == "layer-avatar"
        )
        clip_ids = [c["id"] for c in avatar_layer["clips"]]
        assert "clip-avatar-1" not in clip_ids

    @pytest.mark.asyncio
    async def test_returns_false_for_missing_clip(self, ai_service, mock_project, mock_db):
        """Should return False for non-existent clip."""
        result = await ai_service.delete_clip(mock_project, "nonexistent-clip")
        assert result is False


# =============================================================================
# Semantic Operation Tests
# =============================================================================


class TestSnapToPrevious:
    """Tests for snap_to_previous semantic operation."""

    @pytest.mark.asyncio
    async def test_snaps_to_previous_clip(self, ai_service, mock_project, mock_db):
        """Should move clip to end of previous clip."""
        operation = SemanticOperation(
            operation="snap_to_previous",
            target_clip_id="clip-avatar-2",
        )

        result = await ai_service.execute_semantic_operation(mock_project, operation)

        assert result.success is True
        assert len(result.affected_clip_ids) == 1
        assert "clip-avatar-2" in result.affected_clip_ids

    @pytest.mark.asyncio
    async def test_fails_for_first_clip(self, ai_service, mock_project, mock_db):
        """Should fail when there's no previous clip."""
        operation = SemanticOperation(
            operation="snap_to_previous",
            target_clip_id="clip-avatar-1",
        )

        result = await ai_service.execute_semantic_operation(mock_project, operation)

        assert result.success is False
        assert "No previous clip" in result.error_message


class TestCloseGap:
    """Tests for close_gap semantic operation."""

    @pytest.mark.asyncio
    async def test_closes_gaps_in_layer(self, ai_service, mock_project, mock_db):
        """Should close all gaps in a layer."""
        operation = SemanticOperation(
            operation="close_gap",
            target_layer_id="layer-avatar",
        )

        result = await ai_service.execute_semantic_operation(mock_project, operation)

        assert result.success is True
        # Should have moved clip-avatar-2 from 60000 to 30000
        assert len(result.changes_made) > 0

    @pytest.mark.asyncio
    async def test_fails_for_invalid_layer(self, ai_service, mock_project, mock_db):
        """Should fail for non-existent layer."""
        operation = SemanticOperation(
            operation="close_gap",
            target_layer_id="nonexistent-layer",
        )

        result = await ai_service.execute_semantic_operation(mock_project, operation)

        assert result.success is False
        assert "Layer not found" in result.error_message


class TestAutoDuckBGM:
    """Tests for auto_duck_bgm semantic operation."""

    @pytest.mark.asyncio
    async def test_enables_ducking(self, ai_service, mock_project, mock_db):
        """Should enable BGM ducking."""
        operation = SemanticOperation(
            operation="auto_duck_bgm",
            parameters={
                "duck_to": 0.1,
                "attack_ms": 200,
                "release_ms": 500,
            },
        )

        result = await ai_service.execute_semantic_operation(mock_project, operation)

        assert result.success is True

        # Verify ducking is enabled on BGM track
        bgm_track = next(
            t for t in mock_project.timeline_data["audio_tracks"] if t["type"] == "bgm"
        )
        assert bgm_track["ducking"]["enabled"] is True
        assert bgm_track["ducking"]["duck_to"] == 0.1


# =============================================================================
# Analysis Tests
# =============================================================================


class TestGapAnalysis:
    """Tests for gap analysis."""

    @pytest.mark.asyncio
    async def test_finds_gaps(self, ai_service, mock_project):
        """Should find gaps in timeline."""
        result = await ai_service.analyze_gaps(mock_project)

        # Avatar layer has a gap between 30000-60000
        assert result.total_gaps >= 1
        assert result.total_gap_duration_ms >= 30000

    @pytest.mark.asyncio
    async def test_handles_no_gaps(self, ai_service, mock_project):
        """Should handle timeline with no gaps."""
        # Background layer has no gaps
        mock_project.timeline_data["layers"] = [mock_project.timeline_data["layers"][0]]
        mock_project.timeline_data["audio_tracks"] = []

        result = await ai_service.analyze_gaps(mock_project)

        assert result.total_gaps == 0


class TestPacingAnalysis:
    """Tests for pacing analysis."""

    @pytest.mark.asyncio
    async def test_analyzes_pacing(self, ai_service, mock_project):
        """Should analyze timeline pacing."""
        result = await ai_service.analyze_pacing(mock_project, segment_duration_ms=30000)

        assert result.overall_avg_clip_duration_ms > 0
        assert len(result.segments) > 0

    @pytest.mark.asyncio
    async def test_handles_empty_timeline(self, ai_service, mock_project):
        """Should handle empty timeline."""
        mock_project.duration_ms = 0
        mock_project.timeline_data = {"layers": [], "audio_tracks": []}

        result = await ai_service.analyze_pacing(mock_project)

        assert result.overall_avg_clip_duration_ms == 0
        assert len(result.segments) == 0
