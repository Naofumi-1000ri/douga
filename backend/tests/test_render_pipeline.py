"""Tests for the complete rendering pipeline.

Features:
- Full video rendering from timeline
- Progress tracking
- Job management
- Undo/Redo support
"""

import asyncio
from pathlib import Path
from typing import Optional
from uuid import uuid4

import pytest

from src.render.pipeline import (
    RenderPipeline,
    RenderJob,
    RenderStatus,
    RenderProgress,
    RenderConfig,
    TimelineData,
    UndoManager,
    UndoableAction,
)


class TestRenderStatus:
    """Tests for RenderStatus enum."""

    def test_render_statuses_exist(self):
        """Test that all render statuses exist."""
        assert RenderStatus.PENDING.value == "pending"
        assert RenderStatus.PROCESSING.value == "processing"
        assert RenderStatus.COMPLETED.value == "completed"
        assert RenderStatus.FAILED.value == "failed"
        assert RenderStatus.CANCELLED.value == "cancelled"


class TestRenderProgress:
    """Tests for RenderProgress dataclass."""

    def test_progress_creation(self):
        """Test progress creation."""
        progress = RenderProgress(
            job_id="job123",
            status=RenderStatus.PROCESSING,
            percent=50.0,
            current_step="レイヤー合成中",
            elapsed_ms=5000,
        )
        assert progress.job_id == "job123"
        assert progress.percent == 50.0
        assert progress.current_step == "レイヤー合成中"

    def test_progress_to_dict(self):
        """Test progress serialization."""
        progress = RenderProgress(
            job_id="job123",
            status=RenderStatus.PROCESSING,
            percent=75.0,
        )
        data = progress.to_dict()
        assert data["job_id"] == "job123"
        assert data["status"] == "processing"
        assert data["percent"] == 75.0


class TestRenderConfig:
    """Tests for RenderConfig dataclass."""

    def test_config_defaults(self):
        """Test default render configuration."""
        config = RenderConfig()
        assert config.width == 1920
        assert config.height == 1080
        assert config.fps == 30
        assert config.video_codec == "libx264"
        assert config.audio_codec == "aac"
        assert config.crf == 18

    def test_config_custom(self):
        """Test custom render configuration."""
        config = RenderConfig(
            width=1280,
            height=720,
            fps=60,
            crf=23,
        )
        assert config.width == 1280
        assert config.fps == 60


class TestRenderJob:
    """Tests for RenderJob dataclass."""

    def test_job_creation(self):
        """Test render job creation."""
        job = RenderJob(
            id="job123",
            project_id="proj456",
            status=RenderStatus.PENDING,
            config=RenderConfig(),
        )
        assert job.id == "job123"
        assert job.project_id == "proj456"
        assert job.status == RenderStatus.PENDING

    def test_job_to_dict(self):
        """Test job serialization."""
        job = RenderJob(
            id="job123",
            project_id="proj456",
            status=RenderStatus.COMPLETED,
            output_path="/output/video.mp4",
        )
        data = job.to_dict()
        assert data["id"] == "job123"
        assert data["status"] == "completed"
        assert data["output_path"] == "/output/video.mp4"


class TestTimelineData:
    """Tests for TimelineData dataclass."""

    def test_timeline_creation(self):
        """Test timeline data creation."""
        timeline = TimelineData(
            project_id="proj123",
            duration_ms=60000,
            layers=[
                {"id": "layer1", "type": "background", "clips": []},
                {"id": "layer2", "type": "avatar", "clips": []},
            ],
            audio_tracks=[
                {"id": "audio1", "type": "narration", "clips": []},
            ],
        )
        assert timeline.project_id == "proj123"
        assert timeline.duration_ms == 60000
        assert len(timeline.layers) == 2
        assert len(timeline.audio_tracks) == 1

    def test_timeline_to_dict(self):
        """Test timeline serialization."""
        timeline = TimelineData(
            project_id="proj123",
            duration_ms=30000,
            layers=[],
            audio_tracks=[],
        )
        data = timeline.to_dict()
        assert data["project_id"] == "proj123"
        assert data["duration_ms"] == 30000


class TestRenderPipeline:
    """Tests for RenderPipeline class."""

    def test_create_job(self):
        """Test creating a render job."""
        pipeline = RenderPipeline()
        timeline = TimelineData(
            project_id="proj123",
            duration_ms=10000,
            layers=[],
            audio_tracks=[],
        )

        job = pipeline.create_job(timeline)

        assert job.id is not None
        assert job.project_id == "proj123"
        assert job.status == RenderStatus.PENDING

    def test_get_job(self):
        """Test getting a render job by ID."""
        pipeline = RenderPipeline()
        timeline = TimelineData(
            project_id="proj123",
            duration_ms=10000,
            layers=[],
            audio_tracks=[],
        )

        created_job = pipeline.create_job(timeline)
        retrieved_job = pipeline.get_job(created_job.id)

        assert retrieved_job is not None
        assert retrieved_job.id == created_job.id

    def test_get_nonexistent_job(self):
        """Test getting nonexistent job returns None."""
        pipeline = RenderPipeline()
        job = pipeline.get_job("nonexistent")
        assert job is None

    def test_list_jobs(self):
        """Test listing jobs for a project."""
        pipeline = RenderPipeline()

        # Create multiple jobs
        for i in range(3):
            timeline = TimelineData(
                project_id="proj123",
                duration_ms=10000,
                layers=[],
                audio_tracks=[],
            )
            pipeline.create_job(timeline)

        jobs = pipeline.list_jobs("proj123")
        assert len(jobs) == 3

    def test_cancel_job(self):
        """Test cancelling a pending job."""
        pipeline = RenderPipeline()
        timeline = TimelineData(
            project_id="proj123",
            duration_ms=10000,
            layers=[],
            audio_tracks=[],
        )

        job = pipeline.create_job(timeline)
        result = pipeline.cancel_job(job.id)

        assert result is True
        updated_job = pipeline.get_job(job.id)
        assert updated_job.status == RenderStatus.CANCELLED

    def test_get_progress(self):
        """Test getting job progress."""
        pipeline = RenderPipeline()
        timeline = TimelineData(
            project_id="proj123",
            duration_ms=10000,
            layers=[],
            audio_tracks=[],
        )

        job = pipeline.create_job(timeline)
        progress = pipeline.get_progress(job.id)

        assert progress is not None
        assert progress.job_id == job.id
        assert progress.percent >= 0

    def test_register_progress_callback(self):
        """Test registering progress callback."""
        pipeline = RenderPipeline()
        received_updates = []

        def callback(progress: RenderProgress):
            received_updates.append(progress)

        pipeline.register_progress_callback("job123", callback)

        # Simulate progress update
        pipeline._notify_progress(
            RenderProgress(
                job_id="job123",
                status=RenderStatus.PROCESSING,
                percent=50.0,
            )
        )

        assert len(received_updates) == 1
        assert received_updates[0].percent == 50.0


class TestUndoableAction:
    """Tests for UndoableAction dataclass."""

    def test_action_creation(self):
        """Test undoable action creation."""
        action = UndoableAction(
            id="action123",
            action_type="add_clip",
            description="クリップを追加",
            data={"clip_id": "clip456", "layer_id": "layer1"},
            reverse_data={"clip_id": "clip456"},
        )
        assert action.id == "action123"
        assert action.action_type == "add_clip"
        assert action.data["clip_id"] == "clip456"


class TestUndoManager:
    """Tests for UndoManager class."""

    def test_execute_action(self):
        """Test executing an action."""
        manager = UndoManager()
        action = UndoableAction(
            id="action1",
            action_type="add_clip",
            description="クリップを追加",
            data={},
            reverse_data={},
        )

        manager.execute(action)

        assert manager.can_undo() is True
        assert manager.can_redo() is False

    def test_undo(self):
        """Test undoing an action."""
        manager = UndoManager()
        action = UndoableAction(
            id="action1",
            action_type="add_clip",
            description="クリップを追加",
            data={"value": 1},
            reverse_data={"value": 0},
        )

        manager.execute(action)
        undone = manager.undo()

        assert undone is not None
        assert undone.id == "action1"
        assert manager.can_undo() is False
        assert manager.can_redo() is True

    def test_redo(self):
        """Test redoing an action."""
        manager = UndoManager()
        action = UndoableAction(
            id="action1",
            action_type="add_clip",
            description="クリップを追加",
            data={"value": 1},
            reverse_data={"value": 0},
        )

        manager.execute(action)
        manager.undo()
        redone = manager.redo()

        assert redone is not None
        assert redone.id == "action1"
        assert manager.can_undo() is True
        assert manager.can_redo() is False

    def test_undo_stack_limit(self):
        """Test undo stack has a limit."""
        manager = UndoManager(max_history=5)

        # Execute more actions than limit
        for i in range(10):
            action = UndoableAction(
                id=f"action{i}",
                action_type="test",
                description=f"Action {i}",
                data={},
                reverse_data={},
            )
            manager.execute(action)

        # Should only be able to undo 5 times
        undo_count = 0
        while manager.can_undo():
            manager.undo()
            undo_count += 1

        assert undo_count == 5

    def test_new_action_clears_redo_stack(self):
        """Test that new action clears redo stack."""
        manager = UndoManager()

        # Execute and undo
        action1 = UndoableAction(
            id="action1",
            action_type="test",
            description="Action 1",
            data={},
            reverse_data={},
        )
        manager.execute(action1)
        manager.undo()

        assert manager.can_redo() is True

        # Execute new action
        action2 = UndoableAction(
            id="action2",
            action_type="test",
            description="Action 2",
            data={},
            reverse_data={},
        )
        manager.execute(action2)

        # Redo stack should be cleared
        assert manager.can_redo() is False

    def test_get_undo_description(self):
        """Test getting description of next undo action."""
        manager = UndoManager()
        action = UndoableAction(
            id="action1",
            action_type="add_clip",
            description="クリップを追加",
            data={},
            reverse_data={},
        )

        manager.execute(action)
        desc = manager.get_undo_description()

        assert desc == "クリップを追加"

    def test_get_redo_description(self):
        """Test getting description of next redo action."""
        manager = UndoManager()
        action = UndoableAction(
            id="action1",
            action_type="add_clip",
            description="クリップを追加",
            data={},
            reverse_data={},
        )

        manager.execute(action)
        manager.undo()
        desc = manager.get_redo_description()

        assert desc == "クリップを追加"

    def test_clear_history(self):
        """Test clearing undo/redo history."""
        manager = UndoManager()

        for i in range(3):
            action = UndoableAction(
                id=f"action{i}",
                action_type="test",
                description=f"Action {i}",
                data={},
                reverse_data={},
            )
            manager.execute(action)

        manager.undo()  # Create redo entry

        manager.clear()

        assert manager.can_undo() is False
        assert manager.can_redo() is False
