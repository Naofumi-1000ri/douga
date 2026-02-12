"""Contract tests for suggested_operation structure in timeline analysis.

These tests verify that every suggested_operation returned by
TimelineAnalyzer.generate_suggestions() has a valid, consistent structure
that AI agents can execute without modification.

Specifically tested:
1. All suggested_operations have required fields (endpoint, method, body, description)
2. Endpoints reference real V1 API routes
3. Body fields match the target endpoint's expected schema
4. Each suggestion category maps to the correct endpoint

Run with:
    pytest backend/tests/contract/test_suggested_operations.py -v
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

# Ensure the backend source is importable
BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

from src.services.timeline_analysis import TimelineAnalyzer


# ---------------------------------------------------------------------------
# Known valid V1 endpoint patterns (from ai_v1.py router definitions)
# ---------------------------------------------------------------------------

# These are the actual route paths registered in ai_v1.py with prefix /api/ai/v1.
# The endpoint field in suggested_operation includes "METHOD /api/ai/v1/..." prefix.
VALID_V1_ENDPOINT_PATTERNS: list[re.Pattern] = [
    # clips CRUD
    re.compile(r"^POST /api/ai/v1/projects/[^/]+/clips$"),
    re.compile(r"^PATCH /api/ai/v1/projects/[^/]+/clips/[^/]+/move$"),
    re.compile(r"^PATCH /api/ai/v1/projects/[^/]+/clips/[^/]+/transform$"),
    re.compile(r"^PATCH /api/ai/v1/projects/[^/]+/clips/[^/]+/effects$"),
    re.compile(r"^PATCH /api/ai/v1/projects/[^/]+/clips/[^/]+/crop$"),
    re.compile(r"^PATCH /api/ai/v1/projects/[^/]+/clips/[^/]+/text-style$"),
    re.compile(r"^PATCH /api/ai/v1/projects/[^/]+/clips/[^/]+/timing$"),
    re.compile(r"^PATCH /api/ai/v1/projects/[^/]+/clips/[^/]+/text$"),
    re.compile(r"^PATCH /api/ai/v1/projects/[^/]+/clips/[^/]+/shape$"),
    re.compile(r"^DELETE /api/ai/v1/projects/[^/]+/clips/[^/]+$"),
    re.compile(r"^GET /api/ai/v1/projects/[^/]+/clips/[^/]+$"),
    re.compile(r"^POST /api/ai/v1/projects/[^/]+/clips/[^/]+/split$"),
    re.compile(r"^POST /api/ai/v1/projects/[^/]+/clips/[^/]+/unlink$"),
    re.compile(r"^POST /api/ai/v1/projects/[^/]+/clips/[^/]+/chroma-key/preview$"),
    re.compile(r"^POST /api/ai/v1/projects/[^/]+/clips/[^/]+/chroma-key/apply$"),
    re.compile(r"^POST /api/ai/v1/projects/[^/]+/clips/[^/]+/keyframes$"),
    re.compile(r"^DELETE /api/ai/v1/projects/[^/]+/clips/[^/]+/keyframes/[^/]+$"),
    # audio-clips CRUD
    re.compile(r"^POST /api/ai/v1/projects/[^/]+/audio-clips$"),
    re.compile(r"^PATCH /api/ai/v1/projects/[^/]+/audio-clips/[^/]+/move$"),
    re.compile(r"^PATCH /api/ai/v1/projects/[^/]+/audio-clips/[^/]+$"),
    re.compile(r"^DELETE /api/ai/v1/projects/[^/]+/audio-clips/[^/]+$"),
    re.compile(r"^GET /api/ai/v1/projects/[^/]+/audio-clips/[^/]+$"),
    # audio-tracks
    re.compile(r"^POST /api/ai/v1/projects/[^/]+/audio-tracks$"),
    # layers
    re.compile(r"^POST /api/ai/v1/projects/[^/]+/layers$"),
    re.compile(r"^PATCH /api/ai/v1/projects/[^/]+/layers/[^/]+$"),
    re.compile(r"^PUT /api/ai/v1/projects/[^/]+/layers/order$"),
    # markers
    re.compile(r"^POST /api/ai/v1/projects/[^/]+/markers$"),
    re.compile(r"^PATCH /api/ai/v1/projects/[^/]+/markers/[^/]+$"),
    re.compile(r"^DELETE /api/ai/v1/projects/[^/]+/markers/[^/]+$"),
    # semantic / batch
    re.compile(r"^POST /api/ai/v1/projects/[^/]+/semantic$"),
    re.compile(r"^POST /api/ai/v1/projects/[^/]+/batch$"),
    # history / operations
    re.compile(r"^GET /api/ai/v1/projects/[^/]+/history$"),
    re.compile(r"^GET /api/ai/v1/projects/[^/]+/operations/[^/]+$"),
    re.compile(r"^POST /api/ai/v1/projects/[^/]+/operations/[^/]+/rollback$"),
    # read-only / analysis
    re.compile(r"^GET /api/ai/v1/projects/[^/]+/timeline-overview$"),
    re.compile(r"^GET /api/ai/v1/projects/[^/]+/structure$"),
    re.compile(r"^GET /api/ai/v1/projects/[^/]+/overview$"),
    re.compile(r"^GET /api/ai/v1/projects/[^/]+/summary$"),
    re.compile(r"^GET /api/ai/v1/projects/[^/]+/assets$"),
    re.compile(r"^GET /api/ai/v1/projects/[^/]+/at-time/[^/]+$"),
    re.compile(r"^GET /api/ai/v1/projects/[^/]+/analysis/gaps$"),
    re.compile(r"^GET /api/ai/v1/projects/[^/]+/analysis/pacing$"),
    re.compile(r"^POST /api/ai/v1/projects/[^/]+/preview-diff$"),
    # top-level
    re.compile(r"^GET /api/ai/v1/capabilities$"),
    re.compile(r"^GET /api/ai/v1/version$"),
    re.compile(r"^GET /api/ai/v1/schemas$"),
]


def _is_valid_endpoint(endpoint_str: str) -> bool:
    """Check if an endpoint string matches any known V1 route pattern."""
    return any(p.match(endpoint_str) for p in VALID_V1_ENDPOINT_PATTERNS)


# ---------------------------------------------------------------------------
# Test fixtures — minimal timeline data that triggers each suggestion category
# ---------------------------------------------------------------------------

PROJECT_ID = "test-project-id-for-contracts"


def _make_base_timeline(
    *,
    layers: list[dict] | None = None,
    audio_tracks: list[dict] | None = None,
    duration_ms: int = 0,
) -> dict:
    """Build a minimal timeline dict for testing."""
    return {
        "layers": layers or [],
        "audio_tracks": audio_tracks or [],
        "duration_ms": duration_ms,
    }


def _make_analyzer(timeline: dict, asset_map: dict | None = None) -> TimelineAnalyzer:
    return TimelineAnalyzer(
        timeline_data=timeline,
        asset_map=asset_map or {},
        project_id=PROJECT_ID,
    )


# ---------------------------------------------------------------------------
# Shared assertion helpers
# ---------------------------------------------------------------------------

def assert_suggested_operation_structure(op: dict, *, allow_none: bool = False) -> None:
    """Assert a suggested_operation dict has all required fields and valid types."""
    if allow_none and op is None:
        return

    assert isinstance(op, dict), f"suggested_operation must be a dict, got {type(op)}"

    # Required fields
    assert "endpoint" in op, "Missing 'endpoint' in suggested_operation"
    assert "method" in op, "Missing 'method' in suggested_operation"
    assert "body" in op, "Missing 'body' in suggested_operation"
    assert "description" in op, "Missing 'description' in suggested_operation"

    # Type checks
    assert isinstance(op["endpoint"], str), f"endpoint must be str, got {type(op['endpoint'])}"
    assert isinstance(op["method"], str), f"method must be str, got {type(op['method'])}"
    assert isinstance(op["body"], dict), f"body must be dict, got {type(op['body'])}"
    assert isinstance(op["description"], str), f"description must be str, got {type(op['description'])}"

    # Method must be a valid HTTP method
    assert op["method"] in {"GET", "POST", "PATCH", "PUT", "DELETE"}, (
        f"Invalid method: {op['method']}"
    )

    # Endpoint must not be empty
    assert len(op["endpoint"]) > 0, "endpoint must not be empty"
    assert len(op["description"]) > 0, "description must not be empty"

    # Headers (optional but if present must contain Idempotency-Key)
    if "headers" in op:
        assert isinstance(op["headers"], dict)
        if op["method"] in {"POST", "PATCH", "PUT", "DELETE"}:
            assert "Idempotency-Key" in op["headers"], (
                "Mutation operations must include Idempotency-Key header"
            )


def assert_endpoint_is_valid_v1(endpoint_str: str) -> None:
    """Assert that the endpoint string matches a known V1 route."""
    assert _is_valid_endpoint(endpoint_str), (
        f"Endpoint '{endpoint_str}' does not match any known V1 route. "
        "This may indicate a typo or a reference to a non-existent endpoint."
    )


def assert_project_id_resolved(endpoint_str: str) -> None:
    """Assert project_id placeholders have been resolved (not left as {{project_id}})."""
    assert "{{project_id}}" not in endpoint_str, (
        f"Unresolved {{{{project_id}}}} placeholder in endpoint: {endpoint_str}"
    )


# ---------------------------------------------------------------------------
# Test: Structural validation of all suggested_operations
# ---------------------------------------------------------------------------

class TestSuggestedOperationStructure:
    """Every suggested_operation must have required fields and valid endpoints."""

    def _get_all_suggestions(self, timeline: dict, asset_map: dict | None = None) -> list[dict]:
        analyzer = _make_analyzer(timeline, asset_map)
        return analyzer.generate_suggestions()

    def test_gap_suggestions_have_valid_structure(self) -> None:
        """Gap suggestions for video layers must have valid structure."""
        timeline = _make_base_timeline(
            layers=[{
                "id": "layer-1",
                "name": "Content",
                "type": "content",
                "clips": [
                    {"id": "c1", "start_ms": 0, "duration_ms": 3000},
                    {"id": "c2", "start_ms": 10000, "duration_ms": 3000},
                ],
            }],
            duration_ms=13000,
        )
        suggestions = self._get_all_suggestions(timeline)
        gap_suggestions = [s for s in suggestions if s["category"] == "gap"]
        assert len(gap_suggestions) > 0, "Expected at least one gap suggestion"

        for s in gap_suggestions:
            op = s["suggested_operation"]
            assert_suggested_operation_structure(op)
            assert_endpoint_is_valid_v1(op["endpoint"])
            assert_project_id_resolved(op["endpoint"])

    def test_audio_gap_uses_audio_clips_endpoint(self) -> None:
        """Audio gaps must suggest POST /audio-clips, not POST /clips."""
        timeline = _make_base_timeline(
            layers=[{
                "id": "layer-1",
                "name": "Content",
                "type": "content",
                "clips": [
                    {"id": "c1", "start_ms": 0, "duration_ms": 30000},
                ],
            }],
            audio_tracks=[{
                "id": "track-narr",
                "name": "Narration",
                "type": "narration",
                "clips": [
                    {"id": "ac1", "start_ms": 0, "duration_ms": 5000},
                    {"id": "ac2", "start_ms": 20000, "duration_ms": 5000},
                ],
            }],
            duration_ms=30000,
        )
        suggestions = self._get_all_suggestions(timeline)
        audio_gap_suggestions = [
            s for s in suggestions
            if s["category"] == "gap" and "audio" in s["message"].lower()
        ]
        # There should be audio gap suggestions since there's a gap in narration track
        assert len(audio_gap_suggestions) > 0, "Expected audio gap suggestion"

        for s in audio_gap_suggestions:
            op = s["suggested_operation"]
            assert_suggested_operation_structure(op)
            assert_endpoint_is_valid_v1(op["endpoint"])
            assert "audio-clips" in op["endpoint"], (
                f"Audio gap should use audio-clips endpoint, got: {op['endpoint']}"
            )

    def test_all_suggestions_endpoint_validity(self) -> None:
        """Comprehensive test: trigger many suggestion categories and verify all endpoints."""
        timeline = _make_base_timeline(
            layers=[
                {
                    "id": "layer-bg",
                    "name": "Background",
                    "type": "background",
                    "clips": [
                        {"id": "bg1", "start_ms": 0, "duration_ms": 5000},
                    ],
                },
                {
                    "id": "layer-content",
                    "name": "Content",
                    "type": "content",
                    "clips": [
                        {"id": "c1", "start_ms": 0, "duration_ms": 3000},
                        {"id": "c2", "start_ms": 15000, "duration_ms": 3000},
                    ],
                },
                {
                    "id": "layer-text",
                    "name": "Text",
                    "type": "text",
                    "clips": [],
                },
            ],
            audio_tracks=[
                {
                    "id": "track-narr",
                    "name": "Narration",
                    "type": "narration",
                    "clips": [
                        {"id": "ac1", "start_ms": 0, "duration_ms": 2000},
                    ],
                },
                {
                    "id": "track-bgm",
                    "name": "BGM",
                    "type": "bgm",
                    "clips": [],
                },
            ],
            duration_ms=30000,
        )
        suggestions = self._get_all_suggestions(timeline)
        assert len(suggestions) > 0, "Expected at least some suggestions"

        for s in suggestions:
            op = s.get("suggested_operation")
            if op is None:
                # Some pacing suggestions may have None operation (too_slow without clip id)
                continue
            assert_suggested_operation_structure(op)
            assert_endpoint_is_valid_v1(op["endpoint"])
            assert_project_id_resolved(op["endpoint"])


# ---------------------------------------------------------------------------
# Test: Body schema validation per endpoint
# ---------------------------------------------------------------------------

class TestSuggestedOperationBodySchema:
    """Verify body fields match the expected schema for each endpoint type."""

    def test_post_clips_body_has_clip_key(self) -> None:
        """POST /clips suggestions must have 'clip' key in body."""
        timeline = _make_base_timeline(
            layers=[
                {
                    "id": "layer-bg",
                    "name": "Background",
                    "type": "background",
                    "clips": [
                        {"id": "bg1", "start_ms": 0, "duration_ms": 5000},
                    ],
                },
                {
                    "id": "layer-content",
                    "name": "Content",
                    "type": "content",
                    "clips": [
                        {"id": "c1", "start_ms": 0, "duration_ms": 3000},
                        {"id": "c2", "start_ms": 20000, "duration_ms": 3000},
                    ],
                },
            ],
            duration_ms=30000,
        )
        analyzer = _make_analyzer(timeline)
        suggestions = analyzer.generate_suggestions()

        clips_suggestions = [
            s for s in suggestions
            if s.get("suggested_operation")
            and "/clips" in s["suggested_operation"]["endpoint"]
            and "/audio-clips" not in s["suggested_operation"]["endpoint"]
            and s["suggested_operation"]["method"] == "POST"
            and "/split" not in s["suggested_operation"]["endpoint"]
        ]
        assert len(clips_suggestions) > 0, "Expected POST /clips suggestions"

        for s in clips_suggestions:
            body = s["suggested_operation"]["body"]
            assert "clip" in body, (
                f"POST /clips body must have 'clip' key, got keys: {list(body.keys())} "
                f"for category={s['category']}"
            )

    def test_post_audio_clips_body_has_clip_key(self) -> None:
        """POST /audio-clips suggestions must have 'clip' key in body."""
        timeline = _make_base_timeline(
            layers=[{
                "id": "layer-content",
                "name": "Content",
                "type": "content",
                "clips": [
                    {"id": "c1", "start_ms": 0, "duration_ms": 30000},
                ],
            }],
            audio_tracks=[{
                "id": "track-narr",
                "name": "Narration",
                "type": "narration",
                "clips": [
                    {"id": "ac1", "start_ms": 0, "duration_ms": 3000},
                ],
            }],
            duration_ms=30000,
        )
        analyzer = _make_analyzer(timeline)
        suggestions = analyzer.generate_suggestions()

        audio_clips_suggestions = [
            s for s in suggestions
            if s.get("suggested_operation")
            and "/audio-clips" in s["suggested_operation"]["endpoint"]
            and s["suggested_operation"]["method"] == "POST"
        ]
        assert len(audio_clips_suggestions) > 0, "Expected POST /audio-clips suggestions"

        for s in audio_clips_suggestions:
            body = s["suggested_operation"]["body"]
            assert "clip" in body, (
                f"POST /audio-clips body must have 'clip' key, got keys: {list(body.keys())} "
                f"for category={s['category']}"
            )

    def test_post_semantic_body_has_operation_key(self) -> None:
        """POST /semantic suggestions must have 'operation' key in body."""
        # Create timeline with many short clips to trigger too_fast pacing
        short_clips = [
            {"id": f"c{i}", "start_ms": i * 800, "duration_ms": 800}
            for i in range(20)
        ]
        timeline = _make_base_timeline(
            layers=[{
                "id": "layer-content",
                "name": "Content",
                "type": "content",
                "clips": short_clips,
            }],
            duration_ms=16000,
        )
        analyzer = _make_analyzer(timeline)
        suggestions = analyzer.generate_suggestions()

        semantic_suggestions = [
            s for s in suggestions
            if s.get("suggested_operation")
            and "/semantic" in s["suggested_operation"]["endpoint"]
        ]
        assert len(semantic_suggestions) > 0, (
            "Expected POST /semantic suggestions for too_fast pacing"
        )

        for s in semantic_suggestions:
            body = s["suggested_operation"]["body"]
            assert "operation" in body, (
                f"POST /semantic body must have 'operation' key, got keys: {list(body.keys())} "
                f"for category={s['category']}"
            )

    def test_split_body_has_split_at_ms(self) -> None:
        """POST /clips/{id}/split suggestions must have 'split_at_ms' in body."""
        # Create timeline with many long clips to trigger too_slow pacing
        long_clips = [
            {"id": f"long{i}", "start_ms": i * 20000, "duration_ms": 20000}
            for i in range(5)
        ]
        timeline = _make_base_timeline(
            layers=[{
                "id": "layer-content",
                "name": "Content",
                "type": "content",
                "clips": long_clips,
            }],
            duration_ms=100000,
        )
        analyzer = _make_analyzer(timeline)
        suggestions = analyzer.generate_suggestions()

        split_suggestions = [
            s for s in suggestions
            if s.get("suggested_operation")
            and "/split" in s["suggested_operation"]["endpoint"]
        ]
        assert len(split_suggestions) > 0, (
            "Expected POST /clips/{id}/split suggestions for too_slow pacing"
        )

        for s in split_suggestions:
            op = s["suggested_operation"]
            assert_endpoint_is_valid_v1(op["endpoint"])
            body = op["body"]
            assert "split_at_ms" in body, (
                f"POST /split body must have 'split_at_ms', got keys: {list(body.keys())}"
            )
            assert isinstance(body["split_at_ms"], (int, float)), (
                f"split_at_ms must be numeric, got {type(body['split_at_ms'])}"
            )


# ---------------------------------------------------------------------------
# Test: Category-specific endpoint mapping
# ---------------------------------------------------------------------------

class TestCategoryEndpointMapping:
    """Each suggestion category must map to the correct endpoint."""

    def test_missing_text_section_uses_clips_endpoint(self) -> None:
        """missing_text_section must suggest POST /clips, not POST /semantic."""
        timeline = _make_base_timeline(
            layers=[
                {
                    "id": "layer-content",
                    "name": "Content",
                    "type": "content",
                    "clips": [
                        {"id": "c1", "start_ms": 0, "duration_ms": 10000},
                    ],
                },
                {
                    "id": "layer-text",
                    "name": "Text",
                    "type": "text",
                    "clips": [],
                },
            ],
            audio_tracks=[{
                "id": "track-narr",
                "name": "Narration",
                "type": "narration",
                "clips": [
                    {"id": "ac1", "start_ms": 0, "duration_ms": 10000},
                ],
            }],
            duration_ms=10000,
        )
        analyzer = _make_analyzer(timeline)
        suggestions = analyzer.generate_suggestions()

        mts = [s for s in suggestions if s["category"] == "missing_text_section"]
        assert len(mts) > 0, "Expected missing_text_section suggestion"

        for s in mts:
            op = s["suggested_operation"]
            assert_suggested_operation_structure(op)
            assert "/clips" in op["endpoint"], (
                f"missing_text_section should use /clips endpoint, got: {op['endpoint']}"
            )
            assert "/semantic" not in op["endpoint"], (
                f"missing_text_section must NOT use /semantic endpoint, got: {op['endpoint']}"
            )
            assert "/audio-clips" not in op["endpoint"], (
                f"missing_text_section must NOT use /audio-clips endpoint, got: {op['endpoint']}"
            )

    def test_missing_text_uses_clips_endpoint(self) -> None:
        """missing_text (low priority, layer-level) must suggest POST /clips, not /semantic."""
        timeline = _make_base_timeline(
            layers=[
                {
                    "id": "layer-content",
                    "name": "Content",
                    "type": "content",
                    "clips": [
                        {"id": "c1", "start_ms": 0, "duration_ms": 5000},
                    ],
                },
                {
                    "id": "layer-text",
                    "name": "Text",
                    "type": "text",
                    "clips": [],
                },
            ],
            audio_tracks=[{
                "id": "track-narr",
                "name": "Narration",
                "type": "narration",
                "clips": [
                    {"id": "ac1", "start_ms": 0, "duration_ms": 5000},
                ],
            }],
            duration_ms=5000,
        )
        analyzer = _make_analyzer(timeline)
        suggestions = analyzer.generate_suggestions()

        mt = [s for s in suggestions if s["category"] == "missing_text"]
        assert len(mt) > 0, "Expected missing_text suggestion"

        for s in mt:
            op = s["suggested_operation"]
            assert_suggested_operation_structure(op)
            assert "/clips" in op["endpoint"]
            assert "/semantic" not in op["endpoint"]
            assert "/audio-clips" not in op["endpoint"]

    def test_low_narration_body_has_start_ms_and_duration_ms(self) -> None:
        """low_narration suggestions must include start_ms and duration_ms in body.clip."""
        timeline = _make_base_timeline(
            layers=[{
                "id": "layer-content",
                "name": "Content",
                "type": "content",
                "clips": [
                    {"id": "c1", "start_ms": 0, "duration_ms": 30000},
                ],
            }],
            audio_tracks=[{
                "id": "track-narr",
                "name": "Narration",
                "type": "narration",
                "clips": [
                    {"id": "ac1", "start_ms": 0, "duration_ms": 5000},
                ],
            }],
            duration_ms=30000,
        )
        analyzer = _make_analyzer(timeline)
        suggestions = analyzer.generate_suggestions()

        low_narr = [s for s in suggestions if s["category"] == "low_narration"]
        assert len(low_narr) > 0, "Expected low_narration suggestion"

        for s in low_narr:
            op = s["suggested_operation"]
            assert_suggested_operation_structure(op)
            assert "/audio-clips" in op["endpoint"], (
                f"low_narration should use /audio-clips endpoint, got: {op['endpoint']}"
            )
            # Body must have clip.start_ms and clip.duration_ms
            clip_body = op["body"].get("clip", {})
            assert "start_ms" in clip_body, (
                f"low_narration body.clip must have 'start_ms', got keys: {list(clip_body.keys())}"
            )
            assert "duration_ms" in clip_body, (
                f"low_narration body.clip must have 'duration_ms', got keys: {list(clip_body.keys())}"
            )

    def test_pacing_too_slow_suggests_split(self) -> None:
        """Pacing too_slow must suggest split, and the split operation must not be None."""
        long_clips = [
            {"id": f"long{i}", "start_ms": i * 20000, "duration_ms": 20000}
            for i in range(5)
        ]
        timeline = _make_base_timeline(
            layers=[{
                "id": "layer-content",
                "name": "Content",
                "type": "content",
                "clips": long_clips,
            }],
            duration_ms=100000,
        )
        analyzer = _make_analyzer(timeline)
        suggestions = analyzer.generate_suggestions()

        pacing_slow = [
            s for s in suggestions
            if s["category"] == "pacing" and "too_slow" in s["message"]
        ]
        assert len(pacing_slow) > 0, "Expected pacing too_slow suggestion"

        for s in pacing_slow:
            op = s["suggested_operation"]
            assert op is not None, "too_slow pacing suggested_operation must not be None"
            assert_suggested_operation_structure(op)
            assert "/split" in op["endpoint"], (
                f"too_slow pacing should suggest split, got: {op['endpoint']}"
            )

    def test_audio_gap_uses_audio_clips_not_clips(self) -> None:
        """Audio track gaps must suggest POST /audio-clips, not POST /clips."""
        timeline = _make_base_timeline(
            layers=[{
                "id": "layer-content",
                "name": "Content",
                "type": "content",
                "clips": [
                    {"id": "c1", "start_ms": 0, "duration_ms": 30000},
                ],
            }],
            audio_tracks=[{
                "id": "track-narr",
                "name": "Narration",
                "type": "narration",
                "clips": [
                    {"id": "ac1", "start_ms": 0, "duration_ms": 3000},
                    {"id": "ac2", "start_ms": 25000, "duration_ms": 5000},
                ],
            }],
            duration_ms=30000,
        )
        analyzer = _make_analyzer(timeline)
        suggestions = analyzer.generate_suggestions()

        # Find gap suggestions for the narration track
        audio_gap_sugg = [
            s for s in suggestions
            if s["category"] == "gap"
            and "(audio)" in s["message"]
        ]
        assert len(audio_gap_sugg) > 0, "Expected audio gap suggestions"

        for s in audio_gap_sugg:
            op = s["suggested_operation"]
            assert_suggested_operation_structure(op)
            assert "/audio-clips" in op["endpoint"], (
                f"Audio gap must use /audio-clips, got: {op['endpoint']}"
            )
            # Must NOT use /clips (without audio- prefix)
            endpoint_path = op["endpoint"].split(" ", 1)[-1]  # Strip method prefix
            assert "/audio-clips" in endpoint_path, (
                f"Audio gap endpoint path must contain '/audio-clips', got: {endpoint_path}"
            )


# ---------------------------------------------------------------------------
# Test: Project ID resolution
# ---------------------------------------------------------------------------

class TestProjectIdResolution:
    """Verify project_id placeholders are correctly resolved."""

    def test_project_id_resolved_when_provided(self) -> None:
        """When project_id is set, all endpoints should have it resolved."""
        timeline = _make_base_timeline(
            layers=[
                {
                    "id": "layer-bg",
                    "name": "Background",
                    "type": "background",
                    "clips": [{"id": "bg1", "start_ms": 0, "duration_ms": 5000}],
                },
                {
                    "id": "layer-content",
                    "name": "Content",
                    "type": "content",
                    "clips": [
                        {"id": "c1", "start_ms": 0, "duration_ms": 3000},
                        {"id": "c2", "start_ms": 20000, "duration_ms": 3000},
                    ],
                },
            ],
            duration_ms=30000,
        )
        analyzer = _make_analyzer(timeline)
        suggestions = analyzer.generate_suggestions()

        for s in suggestions:
            op = s.get("suggested_operation")
            if op is None:
                continue
            assert PROJECT_ID in op["endpoint"], (
                f"project_id not resolved in endpoint: {op['endpoint']}"
            )
            assert "{{project_id}}" not in op["endpoint"], (
                f"Unresolved placeholder in endpoint: {op['endpoint']}"
            )

    def test_without_project_id_placeholders_remain(self) -> None:
        """When project_id is None, placeholders should remain (template mode)."""
        timeline = _make_base_timeline(
            layers=[{
                "id": "layer-content",
                "name": "Content",
                "type": "content",
                "clips": [
                    {"id": "c1", "start_ms": 0, "duration_ms": 3000},
                    {"id": "c2", "start_ms": 20000, "duration_ms": 3000},
                ],
            }],
            duration_ms=30000,
        )
        analyzer = TimelineAnalyzer(
            timeline_data=timeline,
            asset_map={},
            project_id=None,
        )
        suggestions = analyzer.generate_suggestions()

        for s in suggestions:
            op = s.get("suggested_operation")
            if op is None:
                continue
            # Without project_id, the {{project_id}} placeholder should remain
            assert "{{project_id}}" in op["endpoint"], (
                f"Expected placeholder to remain when project_id is None: {op['endpoint']}"
            )


# ---------------------------------------------------------------------------
# Test: Idempotency-Key presence
# ---------------------------------------------------------------------------

class TestIdempotencyKey:
    """All mutation operations must include a valid Idempotency-Key header."""

    def test_all_post_operations_have_idempotency_key(self) -> None:
        """POST operations must have Idempotency-Key in headers."""
        timeline = _make_base_timeline(
            layers=[
                {
                    "id": "layer-bg",
                    "name": "Background",
                    "type": "background",
                    "clips": [{"id": "bg1", "start_ms": 0, "duration_ms": 5000}],
                },
                {
                    "id": "layer-content",
                    "name": "Content",
                    "type": "content",
                    "clips": [
                        {"id": "c1", "start_ms": 0, "duration_ms": 3000},
                        {"id": "c2", "start_ms": 20000, "duration_ms": 3000},
                    ],
                },
                {
                    "id": "layer-text",
                    "name": "Text",
                    "type": "text",
                    "clips": [],
                },
            ],
            audio_tracks=[
                {
                    "id": "track-narr",
                    "name": "Narration",
                    "type": "narration",
                    "clips": [
                        {"id": "ac1", "start_ms": 0, "duration_ms": 3000},
                    ],
                },
                {
                    "id": "track-bgm",
                    "name": "BGM",
                    "type": "bgm",
                    "clips": [],
                },
            ],
            duration_ms=30000,
        )
        analyzer = _make_analyzer(timeline)
        suggestions = analyzer.generate_suggestions()
        assert len(suggestions) > 0

        for s in suggestions:
            op = s.get("suggested_operation")
            if op is None:
                continue
            if op["method"] in {"POST", "PATCH", "PUT", "DELETE"}:
                assert "headers" in op, (
                    f"Mutation operation missing 'headers': {op['endpoint']}"
                )
                assert "Idempotency-Key" in op["headers"], (
                    f"Missing Idempotency-Key in headers for: {op['endpoint']}"
                )
                # UUID format check (loose: at least has hyphens and right length)
                idem_key = op["headers"]["Idempotency-Key"]
                assert isinstance(idem_key, str) and len(idem_key) == 36, (
                    f"Idempotency-Key should be UUID format, got: {idem_key}"
                )
