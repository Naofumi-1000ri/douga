"""Focused tests for browser-AI text batch validation paths."""

import asyncio
from unittest.mock import MagicMock

from src.schemas.ai import BatchClipOperation
from src.services.validation_service import ValidationService


def test_validate_batch_operations_update_text():
    """Batch update_text should validate existing text clips."""
    project = MagicMock()
    project.timeline_data = {
        "duration_ms": 60000,
        "layers": [
            {
                "id": "layer-1",
                "clips": [
                    {
                        "id": "clip-text-1",
                        "start_ms": 0,
                        "duration_ms": 3000,
                        "text_content": "元のテキスト",
                    }
                ],
            }
        ],
        "audio_tracks": [],
    }

    service = ValidationService(MagicMock())
    operations = [
        BatchClipOperation(
            operation="update_text",
            clip_id="clip-text",
            clip_type="video",
            data={"text_content": "更新後テキスト"},
        ),
    ]

    result = asyncio.run(service.validate_batch_operations(project, operations))

    assert result.valid is True
    assert result.would_affect.clips_modified == 1


def test_validate_batch_operations_split():
    """Batch split should validate absolute split positions for text clips."""
    project = MagicMock()
    project.timeline_data = {
        "duration_ms": 60000,
        "layers": [
            {
                "id": "layer-1",
                "clips": [
                    {
                        "id": "clip-text-1",
                        "start_ms": 1000,
                        "duration_ms": 4000,
                        "text_content": "分割前テキスト",
                    }
                ],
            }
        ],
        "audio_tracks": [],
    }

    service = ValidationService(MagicMock())
    operations = [
        BatchClipOperation(
            operation="split",
            clip_id="clip-text",
            clip_type="video",
            data={"split_at_ms": 2500},
        ),
    ]

    result = asyncio.run(service.validate_batch_operations(project, operations))

    assert result.valid is True
    assert result.would_affect.clips_created == 1
    assert result.would_affect.clips_modified == 1
