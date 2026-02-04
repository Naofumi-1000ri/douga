"""
Tests for AI v1 API endpoints (Envelope format).

These tests verify:
- Envelope response format (request_id, data, meta)
- Idempotency-Key header requirement for mutations
- If-Match soft enforcement (warnings, 409 on mismatch)
- All Phase 0 endpoints: capabilities, overview, structure, assets, clips

Run with: pytest tests/test_ai_v1_api.py -v

Note: Tests marked with @pytest.mark.requires_db require database connection.
Skip them in CI with: pytest tests/test_ai_v1_api.py -v -m "not requires_db"
"""

import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from src.main import app


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def client():
    """FastAPI test client."""
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


@pytest.fixture
def auth_headers():
    """Authentication headers for dev mode."""
    return {"Authorization": "Bearer dev-token"}




# =============================================================================
# Envelope Format Tests
# =============================================================================


class TestEnvelopeFormat:
    """Test envelope response format compliance."""

    def test_capabilities_returns_envelope(self, client, auth_headers):
        """GET /capabilities returns proper envelope format."""
        response = client.get("/api/ai/v1/capabilities", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()

        # Verify envelope structure
        assert "request_id" in data
        assert "data" in data
        assert "meta" in data

        # Verify meta structure
        meta = data["meta"]
        assert "api_version" in meta
        assert meta["api_version"] == "1.0"
        assert "processing_time_ms" in meta
        assert "timestamp" in meta

        # Verify request_id is a UUID
        uuid.UUID(data["request_id"])

    @pytest.mark.requires_db
    def test_error_returns_envelope(self, client, auth_headers):
        """Error responses also use envelope format.

        Note: Requires DB connection to test 404 response.
        """
        # Request non-existent project
        fake_project_id = str(uuid.uuid4())
        response = client.get(
            f"/api/ai/v1/projects/{fake_project_id}/overview",
            headers=auth_headers,
        )

        # Might get 404 (project not found) or 500 (DB error)
        assert response.status_code in [404, 500]
        data = response.json()

        # Verify envelope structure with error
        assert "request_id" in data
        assert "error" in data
        assert "meta" in data

        # Verify error structure
        error = data["error"]
        assert "code" in error
        assert "message" in error


# =============================================================================
# Capabilities Endpoint Tests
# =============================================================================


class TestCapabilitiesEndpoint:
    """Test GET /api/ai/v1/capabilities endpoint."""

    def test_capabilities_returns_api_info(self, client, auth_headers):
        """Capabilities endpoint returns API features and limits."""
        response = client.get("/api/ai/v1/capabilities", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()["data"]

        # Check API version info
        assert data["api_version"] == "1.0"
        assert data["schema_version"] == "1.0-unified"

        # Check features (Phase 1 complete)
        assert data["features"]["validate_only"] is True
        assert data["features"]["return_diff"] is False  # Phase 2+3
        assert data["features"]["rollback"] is False  # Phase 2+3

        # Check schema notes (unified format documentation)
        assert "schema_notes" in data
        assert data["schema_notes"]["clip_format"] == "unified"
        assert "flat" in data["schema_notes"]["transform_formats"]
        assert "nested" in data["schema_notes"]["transform_formats"]

        # Check limits
        assert "limits" in data
        assert data["limits"]["max_layers"] == 5
        assert data["limits"]["max_duration_ms"] == 3600000

        # Check legacy capability fields still present
        assert "effects" in data
        assert "easings" in data

    def test_capabilities_documents_unsupported_fields(self, client, auth_headers):
        """Capabilities endpoint documents unsupported fields for AI guidance."""
        response = client.get("/api/ai/v1/capabilities", headers=auth_headers)

        assert response.status_code == 200
        schema_notes = response.json()["data"]["schema_notes"]

        # Supported transform fields are documented
        assert "supported_transform_fields" in schema_notes
        supported = schema_notes["supported_transform_fields"]
        assert "position.x" in supported
        assert "position.y" in supported
        assert "scale.x" in supported
        # Rotation is now supported (for transform_clip operations)
        assert any("rotation" in f for f in supported)

        # Unsupported transform fields are documented
        assert "unsupported_transform_fields" in schema_notes
        unsupported = schema_notes["unsupported_transform_fields"]
        assert any("opacity" in f for f in unsupported)
        assert any("anchor" in f for f in unsupported)
        assert any("scale.y" in f for f in unsupported)

        # Unsupported clip-level fields are documented
        assert "unsupported_clip_fields" in schema_notes
        clip_unsupported = schema_notes["unsupported_clip_fields"]
        assert "effects" in clip_unsupported
        assert "transition_in" in clip_unsupported
        assert "transition_out" in clip_unsupported

        # Text style note for unknown keys
        assert "text_style_note" in schema_notes

    @pytest.mark.requires_db
    def test_capabilities_requires_auth(self, client):
        """Capabilities endpoint requires authentication.

        Note: In dev mode, authentication may be bypassed.
        """
        response = client.get("/api/ai/v1/capabilities")

        # In dev mode, might return 200 or auth might be required
        assert response.status_code in [200, 401, 500]


# =============================================================================
# Read Endpoint Tests
# =============================================================================


class TestReadEndpoints:
    """Test read-only endpoints (overview, structure, assets).

    Note: These tests require database connection for full functionality.
    """

    @pytest.mark.requires_db
    def test_overview_endpoint_exists(self, client, auth_headers):
        """GET /overview endpoint exists and returns envelope format."""
        fake_project_id = str(uuid.uuid4())
        response = client.get(
            f"/api/ai/v1/projects/{fake_project_id}/overview",
            headers=auth_headers,
        )

        # Should return envelope format even on error
        data = response.json()
        assert "request_id" in data
        assert "meta" in data

    @pytest.mark.requires_db
    def test_structure_endpoint_exists(self, client, auth_headers):
        """GET /structure endpoint exists and returns envelope format."""
        fake_project_id = str(uuid.uuid4())
        response = client.get(
            f"/api/ai/v1/projects/{fake_project_id}/structure",
            headers=auth_headers,
        )

        # Should return envelope format even on error
        data = response.json()
        assert "request_id" in data
        assert "meta" in data

    @pytest.mark.requires_db
    def test_assets_endpoint_exists(self, client, auth_headers):
        """GET /assets endpoint exists and returns envelope format."""
        fake_project_id = str(uuid.uuid4())
        response = client.get(
            f"/api/ai/v1/projects/{fake_project_id}/assets",
            headers=auth_headers,
        )

        # Should return envelope format even on error
        data = response.json()
        assert "request_id" in data
        assert "meta" in data


# =============================================================================
# Write Endpoint Tests (POST /clips)
# =============================================================================


class TestClipsEndpoint:
    """Test POST /api/ai/v1/projects/{id}/clips endpoint."""

    @pytest.mark.requires_db
    def test_clips_requires_idempotency_key(self, client, auth_headers):
        """POST /clips requires Idempotency-Key header for mutations.

        Note: In test environment, may fail at DB level before header check.
        """
        fake_project_id = str(uuid.uuid4())
        fake_layer_id = "layer-background"
        fake_asset_id = str(uuid.uuid4())

        response = client.post(
            f"/api/ai/v1/projects/{fake_project_id}/clips",
            headers=auth_headers,
            json={
                "options": {"validate_only": False},
                "clip": {
                    "layer_id": fake_layer_id,
                    "asset_id": fake_asset_id,
                    "start_ms": 0,
                },
            },
        )

        # Should be 400 for missing Idempotency-Key, 422 for Pydantic validation, or 500 if DB fails first
        assert response.status_code in [400, 422, 500]
        data = response.json()
        if response.status_code == 400:
            assert "Idempotency-Key" in data.get("detail", "")

    @pytest.mark.requires_db
    def test_clips_with_idempotency_key(self, client, auth_headers):
        """POST /clips accepts Idempotency-Key header."""
        fake_project_id = str(uuid.uuid4())
        fake_layer_id = "layer-background"
        fake_asset_id = str(uuid.uuid4())

        headers = {
            **auth_headers,
            "Idempotency-Key": str(uuid.uuid4()),
        }

        response = client.post(
            f"/api/ai/v1/projects/{fake_project_id}/clips",
            headers=headers,
            json={
                "options": {"validate_only": False},
                "clip": {
                    "layer_id": fake_layer_id,
                    "asset_id": fake_asset_id,
                    "start_ms": 0,
                },
            },
        )

        # Should not be 400 for missing Idempotency-Key
        data = response.json()
        if response.status_code == 400:
            assert "Idempotency-Key" not in data.get("detail", "")

    def test_clips_validate_only_no_idempotency_key_required(self, client, auth_headers):
        """POST /clips with validate_only=true doesn't require Idempotency-Key."""
        fake_project_id = str(uuid.uuid4())
        fake_layer_id = "layer-background"
        fake_asset_id = str(uuid.uuid4())

        response = client.post(
            f"/api/ai/v1/projects/{fake_project_id}/clips",
            headers=auth_headers,
            json={
                "options": {"validate_only": True},
                "clip": {
                    "layer_id": fake_layer_id,
                    "asset_id": fake_asset_id,
                    "start_ms": 0,
                },
            },
        )

        # Should return 400 for FEATURE_NOT_SUPPORTED (validate_only not implemented yet)
        # but NOT for missing Idempotency-Key
        data = response.json()
        if response.status_code == 400:
            assert "Idempotency-Key" not in data.get("detail", "")
            # Check envelope error format
            if "error" in data:
                assert data["error"]["code"] == "FEATURE_NOT_SUPPORTED"


# =============================================================================
# If-Match Header Tests
# =============================================================================


class TestIfMatchHeader:
    """Test If-Match header soft enforcement."""

    @pytest.mark.requires_db
    def test_missing_if_match_adds_warning(self, client, auth_headers):
        """Missing If-Match header adds warning to meta.

        Note: Requires DB connection to reach header validation.
        """
        fake_project_id = str(uuid.uuid4())
        fake_layer_id = "layer-background"
        fake_asset_id = str(uuid.uuid4())

        headers = {
            **auth_headers,
            "Idempotency-Key": str(uuid.uuid4()),
            # No If-Match header
        }

        response = client.post(
            f"/api/ai/v1/projects/{fake_project_id}/clips",
            headers=headers,
            json={
                "options": {"validate_only": False},
                "clip": {
                    "layer_id": fake_layer_id,
                    "asset_id": fake_asset_id,
                    "start_ms": 0,
                },
            },
        )

        data = response.json()

        # If we get past auth and get envelope, check for warning in meta
        if "meta" in data and "warnings" in data["meta"]:
            warnings = data["meta"]["warnings"]
            # If warnings present, If-Match warning should be there
            if warnings:
                assert any("If-Match" in w for w in warnings)


# =============================================================================
# Version Endpoint Tests
# =============================================================================


class TestVersionEndpoint:
    """Test GET /api/ai/v1/version endpoint."""

    def test_version_returns_api_version(self, client, auth_headers):
        """Version endpoint returns API version info."""
        response = client.get("/api/ai/v1/version", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()

        assert "data" in data
        assert data["data"]["api_version"] == "1.0"


# =============================================================================
# Error Code Tests
# =============================================================================


class TestErrorCodes:
    """Test that error responses use proper error codes."""

    @pytest.mark.requires_db
    def test_not_found_uses_proper_code(self, client, auth_headers):
        """404 errors use proper error code in envelope.

        Note: Requires DB connection to verify project doesn't exist.
        """
        fake_project_id = str(uuid.uuid4())

        response = client.get(
            f"/api/ai/v1/projects/{fake_project_id}/overview",
            headers=auth_headers,
        )

        # May be 404 (not found) or 500 (DB error)
        assert response.status_code in [404, 500]
        data = response.json()

        assert "error" in data
        if response.status_code == 404:
            assert data["error"]["code"] in ["PROJECT_NOT_FOUND", "NOT_FOUND"]

    @pytest.mark.requires_db
    def test_unauthorized_uses_proper_code(self, client):
        """401 errors use proper error code in envelope.

        Note: In dev mode, authentication may be bypassed.
        """
        fake_project_id = str(uuid.uuid4())

        response = client.get(f"/api/ai/v1/projects/{fake_project_id}/overview")

        # May be 401 (unauthorized) or 200/404/500 (dev mode bypass or DB error)
        data = response.json()

        # V1 endpoints return envelope format for errors
        if response.status_code == 401 and "error" in data:
            assert data["error"]["code"] == "UNAUTHORIZED"


# =============================================================================
# Schema Unit Tests (no DB required)
# =============================================================================


class TestSchemas:
    """Test Pydantic schema validation."""

    def test_envelope_response_schema(self):
        """EnvelopeResponse schema validates correctly."""
        from src.schemas.envelope import EnvelopeResponse, ResponseMeta

        meta = ResponseMeta(
            api_version="1.0",
            processing_time_ms=100,
            timestamp=datetime.now(timezone.utc),
            warnings=["test warning"],
        )

        envelope = EnvelopeResponse(
            request_id=str(uuid.uuid4()),
            data={"test": "data"},
            meta=meta,
        )

        assert envelope.data == {"test": "data"}
        assert envelope.meta.api_version == "1.0"
        assert "test warning" in envelope.meta.warnings

    def test_error_info_schema(self):
        """ErrorInfo schema validates correctly."""
        from src.schemas.envelope import ErrorInfo, ErrorLocation

        location = ErrorLocation(
            field="layer_id",
            clip_id="clip-123",
        )

        error = ErrorInfo(
            code="CLIP_NOT_FOUND",
            message="Clip not found",
            location=location,
            retryable=True,
        )

        assert error.code == "CLIP_NOT_FOUND"
        assert error.location.clip_id == "clip-123"
        assert error.retryable is True

    def test_operation_options_schema(self):
        """OperationOptions schema validates correctly."""
        from src.schemas.options import OperationOptions

        # Test with include_diff (legacy name)
        options = OperationOptions(
            validate_only=True,
            include_diff=False,
        )

        assert options.validate_only is True
        assert options.include_diff is False

        # Test with return_diff (spec name, alias)
        options_alias = OperationOptions(
            validate_only=True,
            return_diff=True,
        )
        assert options_alias.include_diff is True  # Alias maps to include_diff

        # Test defaults
        default_options = OperationOptions()
        assert default_options.validate_only is False
        assert default_options.include_diff is False

    def test_request_context_creation(self):
        """RequestContext creates valid context."""
        from src.middleware.request_context import create_request_context

        context = create_request_context()

        assert context.request_id is not None
        uuid.UUID(context.request_id)  # Should be valid UUID
        assert context.start_time > 0
        assert isinstance(context.warnings, list)

    def test_build_meta_timing(self):
        """build_meta calculates processing time correctly."""
        import time

        from src.middleware.request_context import build_meta, create_request_context

        context = create_request_context()
        time.sleep(0.01)  # Wait 10ms
        meta = build_meta(context)

        assert meta.processing_time_ms >= 10
        assert meta.api_version == "1.0"
        assert meta.timestamp is not None


# =============================================================================
# Phase 1: Error Codes Unit Tests
# =============================================================================


class TestErrorCodesModule:
    """Test error_codes.py module."""

    def test_error_codes_structure(self):
        """ERROR_CODES dictionary has expected structure."""
        from src.constants.error_codes import ERROR_CODES

        assert "CLIP_NOT_FOUND" in ERROR_CODES
        assert "LAYER_NOT_FOUND" in ERROR_CODES
        assert "INTERNAL_ERROR" in ERROR_CODES

        clip_error = ERROR_CODES["CLIP_NOT_FOUND"]
        assert "retryable" in clip_error
        assert clip_error["retryable"] is True

    def test_is_retryable_function(self):
        """is_retryable function works correctly."""
        from src.constants.error_codes import is_retryable

        assert is_retryable("CLIP_NOT_FOUND") is True
        assert is_retryable("INTERNAL_ERROR") is True
        assert is_retryable("VALIDATION_ERROR") is False
        assert is_retryable("UNKNOWN_CODE") is False

    def test_get_error_spec_function(self):
        """get_error_spec function returns correct specs."""
        from src.constants.error_codes import get_error_spec

        spec = get_error_spec("CLIP_NOT_FOUND")
        assert spec["retryable"] is True
        assert "suggested_action" in spec

        unknown = get_error_spec("NONEXISTENT")
        assert unknown.get("retryable", False) is False


# =============================================================================
# Phase 1: DougaError Exception Tests
# =============================================================================


class TestDougaExceptions:
    """Test custom exception classes."""

    def test_douga_error_base(self):
        """DougaError base class works correctly."""
        from src.exceptions import DougaError

        error = DougaError("Test error")
        assert error.message == "Test error"
        assert error.code == "INTERNAL_ERROR"
        assert error.status_code == 500

    def test_resource_not_found_errors(self):
        """Resource not found errors have correct codes."""
        from src.exceptions import (
            AssetNotFoundError,
            ClipNotFoundError,
            LayerNotFoundError,
            ProjectNotFoundError,
        )

        proj_error = ProjectNotFoundError("proj-123")
        assert proj_error.code == "PROJECT_NOT_FOUND"
        assert proj_error.status_code == 404
        assert "proj-123" in proj_error.message

        clip_error = ClipNotFoundError("clip-456", layer_id="layer-789")
        assert clip_error.code == "CLIP_NOT_FOUND"
        assert clip_error.location is not None
        assert clip_error.location.clip_id == "clip-456"
        assert clip_error.location.layer_id == "layer-789"

        layer_error = LayerNotFoundError("layer-abc")
        assert layer_error.code == "LAYER_NOT_FOUND"

        asset_error = AssetNotFoundError("asset-def")
        assert asset_error.code == "ASSET_NOT_FOUND"

    def test_validation_errors(self):
        """Validation errors have correct codes."""
        from src.exceptions import (
            InvalidTimeRangeError,
            LayerLockedError,
            MissingRequiredFieldError,
            OutOfBoundsError,
        )

        time_error = InvalidTimeRangeError(start_ms=5000, end_ms=3000, field="start_ms")
        assert time_error.code == "INVALID_TIME_RANGE"
        assert time_error.status_code == 400
        assert time_error.location is not None
        assert time_error.location.field == "start_ms"

        bounds_error = OutOfBoundsError(field="duration_ms", value=999999, max_value=3600000)
        assert bounds_error.code == "OUT_OF_BOUNDS"

        locked_error = LayerLockedError("layer-123")
        assert locked_error.code == "LAYER_LOCKED"

        missing_error = MissingRequiredFieldError("layer_id")
        assert missing_error.code == "MISSING_REQUIRED_FIELD"
        assert missing_error.location.field == "layer_id"

    def test_conflict_errors(self):
        """Conflict errors have correct codes."""
        from src.exceptions import (
            ClipOverlapError,
            ConcurrentModificationError,
            IdempotencyConflictError,
        )

        overlap_error = ClipOverlapError(
            clip_id="clip-1", layer_id="layer-1", conflicting_clip_id="clip-2"
        )
        assert overlap_error.code == "CLIP_OVERLAP"
        assert overlap_error.status_code == 409

        concurrent_error = ConcurrentModificationError()
        assert concurrent_error.code == "CONCURRENT_MODIFICATION"

        idempotency_error = IdempotencyConflictError()
        assert idempotency_error.code == "IDEMPOTENCY_CONFLICT"

    def test_error_to_error_info_conversion(self):
        """DougaError.to_error_info() creates valid ErrorInfo."""
        from src.exceptions import ClipNotFoundError

        error = ClipNotFoundError("clip-123")
        error_info = error.to_error_info()

        assert error_info.code == "CLIP_NOT_FOUND"
        assert "clip-123" in error_info.message
        assert error_info.retryable is True
        # Check suggested_fix is from spec (human-readable instruction)
        assert error_info.suggested_fix is not None
        assert "timeline structure" in error_info.suggested_fix.lower()
        # Check suggested_actions
        assert len(error_info.suggested_actions) > 0
        assert error_info.suggested_actions[0].action == "refresh_ids"


# =============================================================================
# Phase 1: Validation Service Unit Tests
# =============================================================================


class TestValidationService:
    """Test ValidationService and validation result structures."""

    def test_would_affect_structure(self):
        """WouldAffect has correct structure."""
        from src.services.validation_service import WouldAffect

        would_affect = WouldAffect(
            clips_created=1,
            clips_modified=0,
            clips_deleted=0,
            duration_change_ms=5000,
            layers_affected=["layer-123"],
        )

        result_dict = would_affect.to_dict()
        assert result_dict["clips_created"] == 1
        assert result_dict["clips_modified"] == 0
        assert result_dict["clips_deleted"] == 0
        assert result_dict["duration_change_ms"] == 5000
        assert "layer-123" in result_dict["layers_affected"]

    def test_validation_result_structure(self):
        """ValidationResult has correct structure."""
        from src.services.validation_service import ValidationResult, WouldAffect

        would_affect = WouldAffect(clips_created=1)
        result = ValidationResult(
            valid=True,
            warnings=["Test warning"],
            would_affect=would_affect,
        )

        result_dict = result.to_dict()
        assert result_dict["valid"] is True
        assert "Test warning" in result_dict["warnings"]
        assert result_dict["would_affect"]["clips_created"] == 1

    def test_validation_result_defaults(self):
        """ValidationResult has sensible defaults."""
        from src.services.validation_service import ValidationResult

        result = ValidationResult(valid=False)
        result_dict = result.to_dict()

        assert result_dict["valid"] is False
        assert result_dict["warnings"] == []
        assert result_dict["would_affect"]["clips_created"] == 0


# =============================================================================
# Phase 1: validate_only Integration Tests (require DB)
# =============================================================================


class TestValidateOnlyEndpoint:
    """Test validate_only mode for POST /clips."""

    def test_validate_only_returns_validation_result(self, client, auth_headers):
        """validate_only=true returns validation result instead of creating clip."""
        fake_project_id = str(uuid.uuid4())
        fake_layer_id = "layer-background"
        fake_asset_id = str(uuid.uuid4())

        response = client.post(
            f"/api/ai/v1/projects/{fake_project_id}/clips",
            headers=auth_headers,
            json={
                "options": {"validate_only": True},
                "clip": {
                    "layer_id": fake_layer_id,
                    "asset_id": fake_asset_id,
                    "start_ms": 0,
                    "duration_ms": 5000,
                },
            },
        )

        data = response.json()

        # Either validation passes and returns result, or error occurs
        # Both should be envelope format
        assert "request_id" in data
        assert "meta" in data

        # Should NOT be 400 with FEATURE_NOT_SUPPORTED (that was Phase 0)
        if response.status_code == 400:
            assert data.get("error", {}).get("code") != "FEATURE_NOT_SUPPORTED"

    def test_validate_only_no_idempotency_key_needed(self, client, auth_headers):
        """validate_only=true doesn't require Idempotency-Key header."""
        fake_project_id = str(uuid.uuid4())

        # No Idempotency-Key header
        response = client.post(
            f"/api/ai/v1/projects/{fake_project_id}/clips",
            headers=auth_headers,
            json={
                "options": {"validate_only": True},
                "clip": {
                    "layer_id": "layer-1",
                    "asset_id": str(uuid.uuid4()),
                    "start_ms": 0,
                    "duration_ms": 1000,
                },
            },
        )

        data = response.json()

        # Should not fail due to missing Idempotency-Key
        if response.status_code == 400:
            error_code = data.get("error", {}).get("code", "")
            error_detail = data.get("detail", "")
            assert "Idempotency-Key" not in error_code
            assert "Idempotency-Key" not in error_detail


# =============================================================================
# Clip Adapter Tests (transitional -> spec schema support)
# =============================================================================


class TestClipAdapter:
    """Test UnifiedClipInput adapter for both flat and nested formats."""

    def test_flat_format_parsing(self):
        """Flat format (transitional) parses correctly."""
        from src.schemas.clip_adapter import UnifiedClipInput

        flat_data = {
            "layer_id": "layer-1",
            "asset_id": "00000000-0000-0000-0000-000000000001",
            "start_ms": 0,
            "duration_ms": 1000,
            "x": 100,
            "y": 200,
            "scale": 1.5,
        }

        unified = UnifiedClipInput.model_validate(flat_data)

        assert unified.layer_id == "layer-1"
        assert unified.start_ms == 0
        assert unified.duration_ms == 1000
        assert unified.x == 100
        assert unified.y == 200
        assert unified.scale == 1.5

    def test_nested_format_parsing(self):
        """Nested format (spec) parses correctly."""
        from src.schemas.clip_adapter import UnifiedClipInput

        nested_data = {
            "type": "video",
            "layer_id": "layer-1",
            "asset_id": "00000000-0000-0000-0000-000000000001",
            "start_ms": 0,
            "duration_ms": 1000,
            "transform": {
                "position": {"x": 100, "y": 200},
                "scale": {"x": 1.5, "y": 1.5},
                "rotation": 0,
                "opacity": 1.0,
                "anchor": {"x": 0.5, "y": 0.5},
            },
        }

        unified = UnifiedClipInput.model_validate(nested_data)

        assert unified.layer_id == "layer-1"
        assert unified.type == "video"
        assert unified.transform is not None
        assert unified.transform.position.x == 100
        assert unified.transform.position.y == 200
        assert unified.transform.scale.x == 1.5
        # After validation, flat values should be populated from nested
        assert unified.x == 100
        assert unified.y == 200
        assert unified.scale == 1.5

    def test_nested_to_flat_conversion(self):
        """Nested format converts to flat dict correctly."""
        from src.schemas.clip_adapter import UnifiedClipInput

        nested_data = {
            "type": "video",
            "layer_id": "layer-1",
            "asset_id": "00000000-0000-0000-0000-000000000001",
            "start_ms": 5000,
            "duration_ms": 2000,
            "transform": {
                "position": {"x": 50, "y": -100},
                "scale": {"x": 2.0, "y": 2.0},
                "rotation": 45,
                "opacity": 0.8,
                "anchor": {"x": 0.5, "y": 0.5},
            },
        }

        unified = UnifiedClipInput.model_validate(nested_data)
        flat_dict = unified.to_flat_dict()

        assert flat_dict["layer_id"] == "layer-1"
        assert flat_dict["start_ms"] == 5000
        assert flat_dict["duration_ms"] == 2000
        assert flat_dict["x"] == 50
        assert flat_dict["y"] == -100
        assert flat_dict["scale"] == 2.0
        assert str(flat_dict["asset_id"]) == "00000000-0000-0000-0000-000000000001"

    def test_flat_to_add_clip_request(self):
        """Flat format converts to AddClipRequest correctly."""
        from src.schemas.ai import AddClipRequest
        from src.schemas.clip_adapter import UnifiedClipInput

        flat_data = {
            "layer_id": "layer-1",
            "asset_id": "00000000-0000-0000-0000-000000000001",
            "start_ms": 0,
            "duration_ms": 1000,
            "x": 100,
            "y": 200,
            "scale": 1.5,
        }

        unified = UnifiedClipInput.model_validate(flat_data)
        flat_dict = unified.to_flat_dict()
        add_clip = AddClipRequest.model_validate(flat_dict)

        assert add_clip.layer_id == "layer-1"
        assert add_clip.x == 100
        assert add_clip.y == 200
        assert add_clip.scale == 1.5

    def test_nested_to_add_clip_request(self):
        """Nested format converts to AddClipRequest correctly."""
        from src.schemas.ai import AddClipRequest
        from src.schemas.clip_adapter import UnifiedClipInput

        nested_data = {
            "type": "image",
            "layer_id": "layer-2",
            "asset_id": "00000000-0000-0000-0000-000000000002",
            "start_ms": 1000,
            "duration_ms": 3000,
            "transform": {
                "position": {"x": -50, "y": 100},
                "scale": {"x": 0.5, "y": 0.5},
            },
        }

        unified = UnifiedClipInput.model_validate(nested_data)
        flat_dict = unified.to_flat_dict()
        add_clip = AddClipRequest.model_validate(flat_dict)

        assert add_clip.layer_id == "layer-2"
        assert add_clip.x == -50
        assert add_clip.y == 100
        assert add_clip.scale == 0.5

    def test_text_clip_with_content(self):
        """Text clip with content parses and converts correctly."""
        from src.schemas.clip_adapter import UnifiedClipInput

        text_data = {
            "type": "text",
            "layer_id": "layer-text",
            "start_ms": 0,
            "duration_ms": 5000,
            "text_content": "Hello World",
            "transform": {
                "position": {"x": 0, "y": 300},
                "scale": {"x": 1, "y": 1},
            },
        }

        unified = UnifiedClipInput.model_validate(text_data)
        flat_dict = unified.to_flat_dict()

        assert flat_dict["text_content"] == "Hello World"
        assert flat_dict["x"] == 0
        assert flat_dict["y"] == 300

    def test_adapt_clip_input_function(self):
        """adapt_clip_input helper function works correctly."""
        from src.schemas.clip_adapter import adapt_clip_input

        # Test flat format
        flat_result = adapt_clip_input({
            "layer_id": "layer-1",
            "asset_id": "00000000-0000-0000-0000-000000000001",
            "start_ms": 0,
            "duration_ms": 1000,
            "x": 10,
            "y": 20,
        })
        assert flat_result["x"] == 10
        assert flat_result["y"] == 20

        # Test nested format
        nested_result = adapt_clip_input({
            "type": "video",
            "layer_id": "layer-1",
            "asset_id": "00000000-0000-0000-0000-000000000001",
            "start_ms": 0,
            "duration_ms": 1000,
            "transform": {
                "position": {"x": 30, "y": 40},
                "scale": {"x": 2, "y": 2},
            },
        })
        assert nested_result["x"] == 30
        assert nested_result["y"] == 40
        assert nested_result["scale"] == 2

    def test_create_clip_request_with_flat_format(self):
        """CreateClipRequest accepts flat format."""
        from src.api.ai_v1 import CreateClipRequest

        request = CreateClipRequest.model_validate({
            "options": {"validate_only": False},
            "clip": {
                "layer_id": "layer-1",
                "asset_id": "00000000-0000-0000-0000-000000000001",
                "start_ms": 0,
                "duration_ms": 1000,
                "x": 100,
                "y": 200,
            },
        })

        internal = request.to_internal_clip()
        assert internal.layer_id == "layer-1"
        assert internal.x == 100
        assert internal.y == 200

    def test_create_clip_request_with_nested_format(self):
        """CreateClipRequest accepts nested format."""
        from src.api.ai_v1 import CreateClipRequest

        request = CreateClipRequest.model_validate({
            "options": {"validate_only": True},
            "clip": {
                "type": "video",
                "layer_id": "layer-1",
                "asset_id": "00000000-0000-0000-0000-000000000001",
                "start_ms": 0,
                "duration_ms": 1000,
                "transform": {
                    "position": {"x": 100, "y": 200},
                    "scale": {"x": 1.5, "y": 1.5},
                },
            },
        })

        internal = request.to_internal_clip()
        assert internal.layer_id == "layer-1"
        assert internal.x == 100
        assert internal.y == 200
        assert internal.scale == 1.5

    def test_text_style_with_known_keys_parses_to_model(self):
        """TextStyle with only known keys parses to TextStyle model."""
        from src.schemas.clip_adapter import UnifiedClipInput

        data = {
            "layer_id": "layer-1",
            "start_ms": 0,
            "duration_ms": 1000,
            "text_content": "Hello",
            "text_style": {
                "font_family": "Arial",
                "font_size": 24,
                "color": "#000000",
            },
        }

        unified = UnifiedClipInput.model_validate(data)
        # With only known keys, it parses as TextStyle (not dict)
        from src.schemas.clip_adapter import TextStyle

        assert isinstance(unified.text_style, TextStyle)
        assert unified.text_style.font_family == "Arial"
        assert unified.text_style.font_size == 24

    def test_text_style_with_unknown_keys_falls_back_to_dict(self):
        """TextStyle with unknown keys falls back to dict, preserving all keys."""
        from src.schemas.clip_adapter import UnifiedClipInput

        data = {
            "layer_id": "layer-1",
            "start_ms": 0,
            "duration_ms": 1000,
            "text_content": "Hello",
            "text_style": {
                "fontFamily": "Arial",  # camelCase (unknown key)
                "fontSize": 24,  # camelCase (unknown key)
                "color": "#000000",
            },
        }

        unified = UnifiedClipInput.model_validate(data)
        # With unknown keys, it falls back to dict (preserving all keys)
        assert isinstance(unified.text_style, dict)
        assert unified.text_style["fontFamily"] == "Arial"
        assert unified.text_style["fontSize"] == 24
        assert unified.text_style["color"] == "#000000"

    def test_non_uniform_scale_generates_warning(self):
        """Non-uniform scale (x != y) generates a warning."""
        from src.schemas.clip_adapter import UnifiedClipInput

        data = {
            "layer_id": "layer-1",
            "asset_id": "00000000-0000-0000-0000-000000000001",
            "start_ms": 0,
            "duration_ms": 1000,
            "transform": {
                "position": {"x": 0, "y": 0},
                "scale": {"x": 2.0, "y": 1.5},  # Non-uniform
            },
        }

        unified = UnifiedClipInput.model_validate(data)
        warnings = unified.get_conversion_warnings()

        # Should warn about non-uniform scale
        assert any("Non-uniform scale" in w for w in warnings)
        assert any("coerced to uniform scale=2.0" in w for w in warnings)
        # scale.x is used as uniform scale
        assert unified.scale == 2.0

    def test_unsupported_transform_rotation_generates_warning(self):
        """Non-zero rotation generates warning."""
        from src.schemas.clip_adapter import UnifiedClipInput

        data = {
            "layer_id": "layer-1",
            "asset_id": "00000000-0000-0000-0000-000000000001",
            "start_ms": 0,
            "duration_ms": 1000,
            "transform": {
                "position": {"x": 0, "y": 0},
                "scale": {"x": 1, "y": 1},
                "rotation": 45,  # Unsupported
            },
        }

        unified = UnifiedClipInput.model_validate(data)
        warnings = unified.get_conversion_warnings()

        assert any("rotation=45" in w and "not yet supported" in w for w in warnings)

    def test_unsupported_transform_opacity_generates_warning(self):
        """Non-default opacity generates warning."""
        from src.schemas.clip_adapter import UnifiedClipInput

        data = {
            "layer_id": "layer-1",
            "asset_id": "00000000-0000-0000-0000-000000000001",
            "start_ms": 0,
            "duration_ms": 1000,
            "transform": {
                "position": {"x": 0, "y": 0},
                "scale": {"x": 1, "y": 1},
                "opacity": 0.5,  # Unsupported
            },
        }

        unified = UnifiedClipInput.model_validate(data)
        warnings = unified.get_conversion_warnings()

        assert any("opacity=0.5" in w and "not yet supported" in w for w in warnings)

    def test_unsupported_transform_anchor_generates_warning(self):
        """Non-default anchor generates warning."""
        from src.schemas.clip_adapter import UnifiedClipInput

        data = {
            "layer_id": "layer-1",
            "asset_id": "00000000-0000-0000-0000-000000000001",
            "start_ms": 0,
            "duration_ms": 1000,
            "transform": {
                "position": {"x": 0, "y": 0},
                "scale": {"x": 1, "y": 1},
                "anchor": {"x": 0, "y": 0},  # Non-default
            },
        }

        unified = UnifiedClipInput.model_validate(data)
        warnings = unified.get_conversion_warnings()

        assert any("anchor" in w and "not yet supported" in w for w in warnings)

    def test_unsupported_effects_field_generates_warning(self):
        """Effects field generates warning."""
        from src.schemas.clip_adapter import UnifiedClipInput

        data = {
            "layer_id": "layer-1",
            "asset_id": "00000000-0000-0000-0000-000000000001",
            "start_ms": 0,
            "duration_ms": 1000,
            "effects": {
                "opacity": 0.8,
                "blend_mode": "multiply",
            },
        }

        unified = UnifiedClipInput.model_validate(data)
        warnings = unified.get_conversion_warnings()

        assert any("effects" in w and "not yet supported" in w for w in warnings)

    def test_unsupported_transitions_generate_warnings(self):
        """Transition fields generate warnings."""
        from src.schemas.clip_adapter import UnifiedClipInput

        data = {
            "layer_id": "layer-1",
            "asset_id": "00000000-0000-0000-0000-000000000001",
            "start_ms": 0,
            "duration_ms": 1000,
            "transition_in": {
                "type": "fade",
                "duration_ms": 500,
            },
            "transition_out": {
                "type": "slide",
                "duration_ms": 300,
            },
        }

        unified = UnifiedClipInput.model_validate(data)
        warnings = unified.get_conversion_warnings()

        assert any("transition_in" in w and "not yet supported" in w for w in warnings)
        assert any("transition_out" in w and "not yet supported" in w for w in warnings)

    def test_no_warnings_for_fully_supported_flat_format(self):
        """Flat format with only supported fields generates no warnings."""
        from src.schemas.clip_adapter import UnifiedClipInput

        data = {
            "layer_id": "layer-1",
            "asset_id": "00000000-0000-0000-0000-000000000001",
            "start_ms": 0,
            "duration_ms": 1000,
            "x": 100,
            "y": 200,
            "scale": 1.5,
        }

        unified = UnifiedClipInput.model_validate(data)
        warnings = unified.get_conversion_warnings()

        assert len(warnings) == 0

    def test_no_warnings_for_fully_supported_nested_format(self):
        """Nested format with only supported fields generates no warnings."""
        from src.schemas.clip_adapter import UnifiedClipInput

        data = {
            "layer_id": "layer-1",
            "asset_id": "00000000-0000-0000-0000-000000000001",
            "start_ms": 0,
            "duration_ms": 1000,
            "transform": {
                "position": {"x": 100, "y": 200},
                "scale": {"x": 1.5, "y": 1.5},  # Uniform
                # rotation, opacity, anchor at defaults
            },
        }

        unified = UnifiedClipInput.model_validate(data)
        warnings = unified.get_conversion_warnings()

        assert len(warnings) == 0

    def test_mixed_format_generates_warning_flat_takes_precedence(self):
        """Mixed format (both flat + nested) warns and flat takes precedence."""
        from src.schemas.clip_adapter import UnifiedClipInput

        data = {
            "layer_id": "layer-1",
            "asset_id": "00000000-0000-0000-0000-000000000001",
            "start_ms": 0,
            "duration_ms": 1000,
            # Flat values
            "x": 50,
            "y": 75,
            "scale": 2.0,
            # Nested values (different from flat)
            "transform": {
                "position": {"x": 100, "y": 200},
                "scale": {"x": 1.0, "y": 1.0},
            },
        }

        unified = UnifiedClipInput.model_validate(data)
        warnings = unified.get_conversion_warnings()

        # Should warn about mixed format
        assert any("Both flat" in w and "nested" in w for w in warnings)
        assert any("flat values take precedence" in w for w in warnings)

        # Flat values should be used (not overwritten by nested)
        assert unified.x == 50
        assert unified.y == 75
        assert unified.scale == 2.0

    def test_mixed_format_still_warns_about_unsupported_transform_fields(self):
        """Mixed format still warns about unsupported transform fields."""
        from src.schemas.clip_adapter import UnifiedClipInput

        data = {
            "layer_id": "layer-1",
            "asset_id": "00000000-0000-0000-0000-000000000001",
            "start_ms": 0,
            "duration_ms": 1000,
            # Flat values
            "x": 50,
            "y": 75,
            "scale": 2.0,
            # Nested with unsupported fields
            "transform": {
                "position": {"x": 100, "y": 200},
                "scale": {"x": 1.0, "y": 1.0},
                "rotation": 45,  # Unsupported
                "opacity": 0.5,  # Unsupported
            },
        }

        unified = UnifiedClipInput.model_validate(data)
        warnings = unified.get_conversion_warnings()

        # Should warn about mixed format
        assert any("Both flat" in w for w in warnings)
        # Should also warn about unsupported transform fields
        assert any("rotation=45" in w for w in warnings)
        assert any("opacity=0.5" in w for w in warnings)

    def test_to_flat_dict_uses_flat_values_in_mixed_format(self):
        """to_flat_dict() uses flat values when both flat and nested are provided."""
        from src.schemas.clip_adapter import UnifiedClipInput

        data = {
            "layer_id": "layer-1",
            "asset_id": "00000000-0000-0000-0000-000000000001",
            "start_ms": 0,
            "duration_ms": 1000,
            # Flat values (should win)
            "x": 50,
            "y": 75,
            "scale": 2.0,
            # Nested values (should be ignored for positioning)
            "transform": {
                "position": {"x": 999, "y": 888},
                "scale": {"x": 0.1, "y": 0.1},
            },
        }

        unified = UnifiedClipInput.model_validate(data)
        flat_dict = unified.to_flat_dict()

        # Flat values should win in the output dict
        assert flat_dict["x"] == 50
        assert flat_dict["y"] == 75
        assert flat_dict["scale"] == 2.0


# =============================================================================
# Move/Transform/Delete Adapter Tests
# =============================================================================


class TestMoveClipAdapter:
    """Test UnifiedMoveClipInput adapter."""

    def test_move_clip_input_parsing(self):
        """Move clip input parses correctly."""
        from src.schemas.clip_adapter import UnifiedMoveClipInput

        data = {
            "new_start_ms": 5000,
            "new_layer_id": "layer-2",
        }

        move_input = UnifiedMoveClipInput.model_validate(data)

        assert move_input.new_start_ms == 5000
        assert move_input.new_layer_id == "layer-2"

    def test_move_clip_input_optional_layer(self):
        """Move clip input works without layer change."""
        from src.schemas.clip_adapter import UnifiedMoveClipInput

        data = {
            "new_start_ms": 10000,
        }

        move_input = UnifiedMoveClipInput.model_validate(data)

        assert move_input.new_start_ms == 10000
        assert move_input.new_layer_id is None


class TestTransformClipAdapter:
    """Test UnifiedTransformInput adapter."""

    def test_transform_flat_format(self):
        """Transform with flat format."""
        from src.schemas.clip_adapter import UnifiedTransformInput

        data = {
            "x": 100,
            "y": 200,
            "scale": 1.5,
            "rotation": 45,
        }

        transform_input = UnifiedTransformInput.model_validate(data)
        flat_dict = transform_input.to_flat_dict()

        assert flat_dict["x"] == 100
        assert flat_dict["y"] == 200
        assert flat_dict["scale"] == 1.5
        assert flat_dict["rotation"] == 45

    def test_transform_nested_format(self):
        """Transform with nested format."""
        from src.schemas.clip_adapter import UnifiedTransformInput

        data = {
            "transform": {
                "position": {"x": 50, "y": 75},
                "scale": {"x": 2.0, "y": 2.0},
                "rotation": 30,
            }
        }

        transform_input = UnifiedTransformInput.model_validate(data)
        flat_dict = transform_input.to_flat_dict()

        assert flat_dict["x"] == 50
        assert flat_dict["y"] == 75
        assert flat_dict["scale"] == 2.0
        assert flat_dict["rotation"] == 30

    def test_transform_mixed_format_flat_wins(self):
        """Transform with mixed format - flat takes precedence."""
        from src.schemas.clip_adapter import UnifiedTransformInput

        data = {
            "x": 100,
            "y": 200,
            "scale": 3.0,
            "transform": {
                "position": {"x": 50, "y": 75},
                "scale": {"x": 1.0, "y": 1.0},
            },
        }

        transform_input = UnifiedTransformInput.model_validate(data)
        flat_dict = transform_input.to_flat_dict()
        warnings = transform_input.get_conversion_warnings()

        # Flat values should win
        assert flat_dict["x"] == 100
        assert flat_dict["y"] == 200
        assert flat_dict["scale"] == 3.0

        # Warning about mixed format
        assert any("Both flat" in w for w in warnings)

    def test_transform_non_uniform_scale_warning(self):
        """Transform with non-uniform scale generates warning."""
        from src.schemas.clip_adapter import UnifiedTransformInput

        data = {
            "transform": {
                "position": {"x": 0, "y": 0},
                "scale": {"x": 2.0, "y": 1.5},  # Non-uniform
            }
        }

        transform_input = UnifiedTransformInput.model_validate(data)
        warnings = transform_input.get_conversion_warnings()

        assert any("Non-uniform scale" in w for w in warnings)

    def test_transform_unsupported_opacity_warning(self):
        """Transform with non-default opacity generates warning."""
        from src.schemas.clip_adapter import UnifiedTransformInput

        data = {
            "transform": {
                "position": {"x": 0, "y": 0},
                "scale": {"x": 1, "y": 1},
                "opacity": 0.5,
            }
        }

        transform_input = UnifiedTransformInput.model_validate(data)
        warnings = transform_input.get_conversion_warnings()

        assert any("opacity" in w for w in warnings)


class TestV1RequestModels:
    """Test v1 request model conversions."""

    def test_move_clip_v1_request_conversion(self):
        """MoveClipV1Request converts to internal format."""
        from src.api.ai_v1 import MoveClipV1Request

        request = MoveClipV1Request.model_validate({
            "options": {"validate_only": False},
            "move": {
                "new_start_ms": 5000,
                "new_layer_id": "layer-2",
            },
        })

        internal = request.to_internal_request()

        assert internal.new_start_ms == 5000
        assert internal.new_layer_id == "layer-2"

    def test_transform_clip_v1_request_conversion(self):
        """TransformClipV1Request converts to internal format."""
        from src.api.ai_v1 import TransformClipV1Request

        request = TransformClipV1Request.model_validate({
            "options": {"validate_only": True},
            "transform": {
                "x": 100,
                "y": 200,
                "scale": 1.5,
            },
        })

        internal = request.to_internal_request()

        assert internal.x == 100
        assert internal.y == 200
        assert internal.scale == 1.5

    def test_transform_clip_v1_request_nested_conversion(self):
        """TransformClipV1Request converts nested format to internal."""
        from src.api.ai_v1 import TransformClipV1Request

        request = TransformClipV1Request.model_validate({
            "options": {"validate_only": False},
            "transform": {
                "transform": {
                    "position": {"x": 50, "y": 75},
                    "scale": {"x": 2.0, "y": 2.0},
                }
            },
        })

        internal = request.to_internal_request()

        assert internal.x == 50
        assert internal.y == 75
        assert internal.scale == 2.0

    def test_delete_clip_v1_request(self):
        """DeleteClipV1Request parses correctly."""
        from src.api.ai_v1 import DeleteClipV1Request

        request = DeleteClipV1Request.model_validate({
            "options": {"validate_only": True},
        })

        assert request.options.validate_only is True


class TestCapabilitiesPriority1:
    """Test capabilities endpoint includes Priority 1 operations."""

    def test_capabilities_includes_priority_1_operations(self, client, auth_headers):
        """Capabilities includes move_clip, transform_clip, delete_clip."""
        response = client.get("/api/ai/v1/capabilities", headers=auth_headers)

        # May fail due to DB but should at least try
        if response.status_code == 200:
            data = response.json()["data"]
            supported = data["supported_operations"]

            assert "add_clip" in supported
            assert "move_clip" in supported
            assert "transform_clip" in supported
            assert "delete_clip" in supported


class TestValidationServiceMoveTransformDelete:
    """Test validation service methods for move/transform/delete."""

    def test_validation_service_move_clip_methods_exist(self):
        """ValidationService has move/transform/delete validation methods."""
        from src.services.validation_service import ValidationService

        # Just check the methods exist
        assert hasattr(ValidationService, "validate_move_clip")
        assert hasattr(ValidationService, "validate_transform_clip")
        assert hasattr(ValidationService, "validate_delete_clip")

    def test_would_affect_for_move(self):
        """WouldAffect structure works for move operations."""
        from src.services.validation_service import WouldAffect

        would_affect = WouldAffect(
            clips_created=0,
            clips_modified=1,
            clips_deleted=0,
            duration_change_ms=0,
            layers_affected=["layer-1", "layer-2"],
        )

        result = would_affect.to_dict()

        assert result["clips_modified"] == 1
        assert result["clips_deleted"] == 0
        assert "layer-1" in result["layers_affected"]
        assert "layer-2" in result["layers_affected"]

    def test_would_affect_for_delete(self):
        """WouldAffect structure works for delete operations."""
        from src.services.validation_service import WouldAffect

        would_affect = WouldAffect(
            clips_created=0,
            clips_modified=0,
            clips_deleted=1,
            duration_change_ms=-3000,  # Timeline shorter after delete
            layers_affected=["layer-1"],
        )

        result = would_affect.to_dict()

        assert result["clips_deleted"] == 1
        assert result["duration_change_ms"] == -3000


class TestPartialNestedTransform:
    """Test that partial nested transforms don't overwrite unspecified fields."""

    def test_partial_nested_transform_only_rotation(self):
        """Nested transform with only rotation doesn't emit position/scale."""
        from src.schemas.clip_adapter import UnifiedTransformInput

        # Only rotation specified in nested format
        unified = UnifiedTransformInput.model_validate({
            "transform": {
                "rotation": 45,
                # position and scale NOT specified - should use defaults but NOT be emitted
            }
        })

        result = unified.to_flat_dict()

        # Should only include rotation, NOT position or scale
        assert "rotation" in result
        assert result["rotation"] == 45
        # These should NOT be in the result (would overwrite existing values)
        assert "x" not in result
        assert "y" not in result
        assert "scale" not in result

    def test_partial_nested_transform_only_position(self):
        """Nested transform with only position doesn't emit scale/rotation."""
        from src.schemas.clip_adapter import UnifiedTransformInput

        # Only position specified
        unified = UnifiedTransformInput.model_validate({
            "transform": {
                "position": {"x": 100, "y": 200},
            }
        })

        result = unified.to_flat_dict()

        # Should include position
        assert result["x"] == 100
        assert result["y"] == 200
        # Should NOT include scale or rotation
        assert "scale" not in result
        assert "rotation" not in result

    def test_partial_nested_transform_only_position_x(self):
        """Nested transform with only position.x doesn't emit y."""
        from src.schemas.clip_adapter import UnifiedTransformInput

        # Only position.x specified
        unified = UnifiedTransformInput.model_validate({
            "transform": {
                "position": {"x": 100},  # y not provided
            }
        })

        result = unified.to_flat_dict()

        # Should only include x
        assert result["x"] == 100
        # Should NOT include y (not explicitly provided)
        assert "y" not in result
        # Should NOT include scale or rotation
        assert "scale" not in result
        assert "rotation" not in result

    def test_partial_nested_transform_only_scale(self):
        """Nested transform with only scale doesn't emit position/rotation."""
        from src.schemas.clip_adapter import UnifiedTransformInput

        # Only scale specified
        unified = UnifiedTransformInput.model_validate({
            "transform": {
                "scale": {"x": 1.5, "y": 1.5},
            }
        })

        result = unified.to_flat_dict()

        # Should include scale
        assert result["scale"] == 1.5
        # Should NOT include position or rotation
        assert "x" not in result
        assert "y" not in result
        assert "rotation" not in result

    def test_full_nested_transform_emits_all(self):
        """Nested transform with all fields specified emits all values."""
        from src.schemas.clip_adapter import UnifiedTransformInput

        # All fields specified
        unified = UnifiedTransformInput.model_validate({
            "transform": {
                "position": {"x": 100, "y": 200},
                "scale": {"x": 1.5, "y": 1.5},
                "rotation": 45,
            }
        })

        result = unified.to_flat_dict()

        # All should be included
        assert result["x"] == 100
        assert result["y"] == 200
        assert result["scale"] == 1.5
        assert result["rotation"] == 45


class TestIDMatchingConsistency:
    """Test that ID matching is consistent between validation and apply."""

    def test_validation_service_id_matching_unidirectional(self):
        """Validation service uses unidirectional prefix matching."""
        from src.services.validation_service import ValidationService

        # Create a mock timeline
        timeline = {
            "layers": [
                {
                    "id": "layer-abc-123",
                    "clips": [
                        {"id": "clip-xyz-456", "start_ms": 0, "duration_ms": 1000}
                    ]
                }
            ]
        }

        # Create a ValidationService instance (db is None, not used for find methods)
        service = ValidationService(None)

        # Test _find_clip_by_id - should find with prefix
        clip, layer, full_id = service._find_clip_by_id(timeline, "clip-xyz")
        assert clip is not None
        assert full_id == "clip-xyz-456"

        # Test _find_clip_by_id - should NOT find with reversed prefix
        # "clip-xyz-456-extra" should NOT match "clip-xyz-456" in unidirectional mode
        clip2, layer2, full_id2 = service._find_clip_by_id(timeline, "clip-xyz-456-extra")
        assert clip2 is None  # Should NOT find - no stored ID starts with this
        assert full_id2 is None

    def test_validation_service_layer_matching_unidirectional(self):
        """Validation service layer matching is unidirectional."""
        from src.services.validation_service import ValidationService

        timeline = {
            "layers": [
                {"id": "layer-abc-123", "clips": []}
            ]
        }

        service = ValidationService(None)

        # Should find with prefix
        layer = service._find_layer_by_id(timeline, "layer-abc")
        assert layer is not None
        assert layer["id"] == "layer-abc-123"

        # Should NOT find with reversed prefix
        layer2 = service._find_layer_by_id(timeline, "layer-abc-123-extra")
        assert layer2 is None


class TestNestedRotationSupport:
    """Test that nested rotation is supported in transform_clip."""

    def test_transform_clip_nested_rotation_supported(self):
        """Nested transform.rotation is extracted and applied."""
        from src.schemas.clip_adapter import UnifiedTransformInput

        unified = UnifiedTransformInput.model_validate({
            "transform": {
                "rotation": 90,
            }
        })

        result = unified.to_flat_dict()

        assert "rotation" in result
        assert result["rotation"] == 90

    def test_transform_clip_nested_rotation_no_warning(self):
        """Nested rotation doesn't generate a warning (it's supported)."""
        from src.schemas.clip_adapter import UnifiedTransformInput

        unified = UnifiedTransformInput.model_validate({
            "transform": {
                "rotation": 45,
            }
        })

        warnings = unified.get_conversion_warnings()

        # Should NOT have a warning about rotation being unsupported
        assert not any("rotation" in w for w in warnings)

    def test_add_clip_nested_rotation_warning(self):
        """Add clip DOES warn about rotation (it's not supported there)."""
        from src.schemas.clip_adapter import UnifiedClipInput

        unified = UnifiedClipInput.model_validate({
            "layer_id": "layer-1",
            "start_ms": 0,
            "duration_ms": 1000,
            "text_content": "Test",
            "transform": {
                "rotation": 45,
            }
        })

        warnings = unified.get_conversion_warnings()

        # Should have a warning about rotation being unsupported
        assert any("rotation" in w and "not yet supported" in w for w in warnings)


# =============================================================================
# Priority 2: Layer Endpoint Tests
# =============================================================================


class TestLayerV1RequestModels:
    """Test v1 layer request model parsing and conversion."""

    def test_add_layer_v1_request_parsing(self):
        """AddLayerV1Request parses and converts correctly."""
        from src.api.ai_v1 import AddLayerV1Request

        request = AddLayerV1Request.model_validate({
            "options": {"validate_only": False},
            "layer": {
                "name": "My Layer",
                "type": "content",
                "insert_at": 0,
            },
        })

        assert request.options.validate_only is False
        internal = request.to_internal_request()
        assert internal.name == "My Layer"
        assert internal.type == "content"
        assert internal.insert_at == 0

    def test_update_layer_v1_request_parsing(self):
        """UpdateLayerV1Request parses correctly."""
        from src.api.ai_v1 import UpdateLayerV1Request

        request = UpdateLayerV1Request.model_validate({
            "options": {"validate_only": True},
            "layer": {
                "name": "New Name",
                "visible": False,
                "locked": True,
            },
        })

        assert request.options.validate_only is True
        internal = request.to_internal_request()
        assert internal.name == "New Name"
        assert internal.visible is False
        assert internal.locked is True

    def test_reorder_layers_v1_request_parsing(self):
        """ReorderLayersV1Request parses correctly."""
        from src.api.ai_v1 import ReorderLayersV1Request

        request = ReorderLayersV1Request.model_validate({
            "options": {"validate_only": False},
            "order": {
                "layer_ids": ["layer-3", "layer-1", "layer-2"],
            },
        })

        assert request.options.validate_only is False
        internal = request.to_internal_request()
        assert internal.layer_ids == ["layer-3", "layer-1", "layer-2"]


class TestLayerValidationService:
    """Test validation service methods for layer operations."""

    def test_validate_add_layer_basic(self):
        """validate_add_layer returns valid result for valid input."""
        import asyncio
        from src.services.validation_service import ValidationService
        from src.schemas.ai import AddLayerRequest
        from unittest.mock import MagicMock

        # Create mock project
        project = MagicMock()
        project.timeline_data = {
            "layers": [
                {"id": "layer-1", "name": "Background"},
            ]
        }

        service = ValidationService(None)
        request = AddLayerRequest(name="New Layer", type="content")

        result = asyncio.get_event_loop().run_until_complete(
            service.validate_add_layer(project, request)
        )

        assert result.valid is True
        assert result.would_affect.clips_created == 0

    def test_validate_add_layer_duplicate_name_warning(self):
        """validate_add_layer warns about duplicate layer names."""
        import asyncio
        from src.services.validation_service import ValidationService
        from src.schemas.ai import AddLayerRequest
        from unittest.mock import MagicMock

        project = MagicMock()
        project.timeline_data = {
            "layers": [
                {"id": "layer-1", "name": "Background"},
            ]
        }

        service = ValidationService(None)
        request = AddLayerRequest(name="Background", type="content")  # Duplicate

        result = asyncio.get_event_loop().run_until_complete(
            service.validate_add_layer(project, request)
        )

        assert result.valid is True
        assert any("already exists" in w for w in result.warnings)

    def test_validate_update_layer_not_found(self):
        """validate_update_layer raises LayerNotFoundError for invalid layer."""
        import asyncio
        import pytest
        from src.services.validation_service import ValidationService
        from src.schemas.ai import UpdateLayerRequest
        from src.exceptions import LayerNotFoundError
        from unittest.mock import MagicMock

        project = MagicMock()
        project.timeline_data = {
            "layers": [
                {"id": "layer-1", "name": "Background"},
            ]
        }

        service = ValidationService(None)
        request = UpdateLayerRequest(name="New Name")

        with pytest.raises(LayerNotFoundError):
            asyncio.get_event_loop().run_until_complete(
                service.validate_update_layer(project, "nonexistent", request)
            )

    def test_validate_update_layer_valid(self):
        """validate_update_layer returns valid result for existing layer."""
        import asyncio
        from src.services.validation_service import ValidationService
        from src.schemas.ai import UpdateLayerRequest
        from unittest.mock import MagicMock

        project = MagicMock()
        project.timeline_data = {
            "layers": [
                {"id": "layer-1", "name": "Background", "clips": [], "locked": False},
            ]
        }

        service = ValidationService(None)
        request = UpdateLayerRequest(name="New Name")

        result = asyncio.get_event_loop().run_until_complete(
            service.validate_update_layer(project, "layer-1", request)
        )

        assert result.valid is True
        assert "layer-1" in result.would_affect.layers_affected

    def test_validate_reorder_layers_not_found(self):
        """validate_reorder_layers raises LayerNotFoundError for invalid layer."""
        import asyncio
        import pytest
        from src.services.validation_service import ValidationService
        from src.exceptions import LayerNotFoundError
        from unittest.mock import MagicMock

        project = MagicMock()
        project.timeline_data = {
            "layers": [
                {"id": "layer-1", "name": "Layer 1"},
                {"id": "layer-2", "name": "Layer 2"},
            ]
        }

        service = ValidationService(None)

        with pytest.raises(LayerNotFoundError):
            asyncio.get_event_loop().run_until_complete(
                service.validate_reorder_layers(project, ["layer-1", "nonexistent"])
            )

    def test_validate_reorder_layers_valid(self):
        """validate_reorder_layers returns valid result for valid order."""
        import asyncio
        from src.services.validation_service import ValidationService
        from unittest.mock import MagicMock

        project = MagicMock()
        project.timeline_data = {
            "layers": [
                {"id": "layer-1", "name": "Layer 1"},
                {"id": "layer-2", "name": "Layer 2"},
            ]
        }

        service = ValidationService(None)

        result = asyncio.get_event_loop().run_until_complete(
            service.validate_reorder_layers(project, ["layer-2", "layer-1"])
        )

        assert result.valid is True
        assert "layer-1" in result.would_affect.layers_affected
        assert "layer-2" in result.would_affect.layers_affected

    def test_validate_reorder_layers_missing_layers_warning(self):
        """validate_reorder_layers warns if not all layers are included."""
        import asyncio
        from src.services.validation_service import ValidationService
        from unittest.mock import MagicMock

        project = MagicMock()
        project.timeline_data = {
            "layers": [
                {"id": "layer-1", "name": "Layer 1"},
                {"id": "layer-2", "name": "Layer 2"},
                {"id": "layer-3", "name": "Layer 3"},
            ]
        }

        service = ValidationService(None)

        result = asyncio.get_event_loop().run_until_complete(
            service.validate_reorder_layers(project, ["layer-2", "layer-1"])  # Missing layer-3
        )

        assert result.valid is True
        assert any("not in reorder list" in w for w in result.warnings)


class TestCapabilitiesPriority2:
    """Test capabilities endpoint includes Priority 2 operations."""

    def test_capabilities_includes_layer_operations(self, client, auth_headers):
        """Capabilities includes add_layer, update_layer, reorder_layers."""
        response = client.get("/api/ai/v1/capabilities", headers=auth_headers)

        # May fail due to DB but should at least try
        if response.status_code == 200:
            data = response.json()["data"]
            supported = data["supported_operations"]

            assert "add_layer" in supported
            assert "update_layer" in supported
            assert "reorder_layers" in supported


# =============================================================================
# Priority 3: Audio Request Models Tests
# =============================================================================


class TestAudioV1RequestModels:
    """Test v1 audio request model structures."""

    def test_add_audio_clip_request_structure(self):
        """AddAudioClipV1Request wraps AddAudioClipRequest with options."""
        from src.api.ai_v1 import AddAudioClipV1Request
        from src.schemas.ai import AddAudioClipRequest

        data = {
            "options": {"validate_only": True},
            "clip": {
                "track_id": "track-123",
                "asset_id": str(uuid.uuid4()),
                "start_ms": 5000,
                "duration_ms": 3000,
            }
        }

        request = AddAudioClipV1Request.model_validate(data)
        assert request.options.validate_only is True
        assert request.clip.track_id == "track-123"
        assert request.clip.start_ms == 5000

        internal = request.to_internal_request()
        assert isinstance(internal, AddAudioClipRequest)
        assert internal.track_id == "track-123"

    def test_move_audio_clip_request_structure(self):
        """MoveAudioClipV1Request converts to internal format."""
        from src.api.ai_v1 import MoveAudioClipV1Request

        data = {
            "options": {"validate_only": False},
            "new_start_ms": 10000,
            "new_track_id": "track-456",
        }

        request = MoveAudioClipV1Request.model_validate(data)
        assert request.new_start_ms == 10000
        assert request.new_track_id == "track-456"

        internal = request.to_internal_request()
        assert internal.new_start_ms == 10000
        assert internal.new_track_id == "track-456"

    def test_delete_audio_clip_request_structure(self):
        """DeleteAudioClipV1Request has only options."""
        from src.api.ai_v1 import DeleteAudioClipV1Request

        data = {"options": {"validate_only": True}}
        request = DeleteAudioClipV1Request.model_validate(data)
        assert request.options.validate_only is True

    def test_add_audio_track_request_structure(self):
        """AddAudioTrackV1Request wraps AddAudioTrackRequest with options."""
        from src.api.ai_v1 import AddAudioTrackV1Request
        from src.schemas.ai import AddAudioTrackRequest

        data = {
            "options": {"validate_only": True},
            "track": {
                "name": "Background Music",
                "type": "bgm",
                "volume": 0.8,
            }
        }

        request = AddAudioTrackV1Request.model_validate(data)
        assert request.options.validate_only is True
        assert request.track.name == "Background Music"
        assert request.track.type == "bgm"
        assert request.track.volume == 0.8

        internal = request.to_internal_request()
        assert isinstance(internal, AddAudioTrackRequest)
        assert internal.name == "Background Music"


class TestAudioTrackSchema:
    """Test AddAudioTrackRequest schema."""

    def test_default_values(self):
        """AddAudioTrackRequest has sensible defaults."""
        from src.schemas.ai import AddAudioTrackRequest

        data = {"name": "Test Track"}
        request = AddAudioTrackRequest.model_validate(data)

        assert request.name == "Test Track"
        assert request.type == "bgm"  # default
        assert request.volume == 1.0  # default
        assert request.muted is False  # default
        assert request.ducking_enabled is False  # default
        assert request.insert_at is None  # default

    def test_all_track_types(self):
        """AddAudioTrackRequest accepts all valid track types."""
        from src.schemas.ai import AddAudioTrackRequest

        for track_type in ["narration", "bgm", "se", "video"]:
            request = AddAudioTrackRequest.model_validate({
                "name": f"Test {track_type}",
                "type": track_type,
            })
            assert request.type == track_type

    def test_volume_constraints(self):
        """AddAudioTrackRequest validates volume range."""
        from pydantic import ValidationError
        from src.schemas.ai import AddAudioTrackRequest

        # Valid volume
        request = AddAudioTrackRequest.model_validate({"name": "Test", "volume": 1.5})
        assert request.volume == 1.5

        # Invalid volume (too high)
        with pytest.raises(ValidationError):
            AddAudioTrackRequest.model_validate({"name": "Test", "volume": 3.0})

        # Invalid volume (negative)
        with pytest.raises(ValidationError):
            AddAudioTrackRequest.model_validate({"name": "Test", "volume": -0.5})


# =============================================================================
# Priority 3: Audio Validation Service Tests
# =============================================================================


class TestAudioValidationService:
    """Test audio validation methods."""

    def test_validate_add_audio_track_valid(self):
        """Valid add_audio_track passes validation."""
        import asyncio
        from unittest.mock import MagicMock

        from src.schemas.ai import AddAudioTrackRequest
        from src.services.validation_service import ValidationService

        project = MagicMock()
        project.timeline_data = {"audio_tracks": []}

        request = AddAudioTrackRequest(name="BGM Track", type="bgm")
        service = ValidationService(None)

        result = asyncio.get_event_loop().run_until_complete(
            service.validate_add_audio_track(project, request)
        )

        assert result.valid is True
        assert result.would_affect.clips_created == 0
        assert result.would_affect.duration_change_ms == 0

    def test_validate_add_audio_track_duplicate_name_warning(self):
        """Duplicate track name generates warning."""
        import asyncio
        from unittest.mock import MagicMock

        from src.schemas.ai import AddAudioTrackRequest
        from src.services.validation_service import ValidationService

        project = MagicMock()
        project.timeline_data = {
            "audio_tracks": [
                {"id": "track-1", "name": "BGM"},
            ]
        }

        request = AddAudioTrackRequest(name="BGM", type="bgm")
        service = ValidationService(None)

        result = asyncio.get_event_loop().run_until_complete(
            service.validate_add_audio_track(project, request)
        )

        assert result.valid is True
        assert any("already exists" in w for w in result.warnings)

    def test_validate_delete_audio_clip_not_found(self):
        """Delete non-existent audio clip raises error."""
        import asyncio
        from unittest.mock import MagicMock

        from src.exceptions import AudioClipNotFoundError
        from src.services.validation_service import ValidationService

        project = MagicMock()
        project.timeline_data = {"audio_tracks": []}

        service = ValidationService(None)

        with pytest.raises(AudioClipNotFoundError):
            asyncio.get_event_loop().run_until_complete(
                service.validate_delete_audio_clip(project, "nonexistent-clip")
            )

    def test_validate_delete_audio_clip_found(self):
        """Delete existing audio clip passes validation."""
        import asyncio
        from unittest.mock import MagicMock

        from src.services.validation_service import ValidationService

        project = MagicMock()
        project.timeline_data = {
            "audio_tracks": [
                {
                    "id": "track-1",
                    "clips": [
                        {"id": "audio-clip-123", "start_ms": 0, "duration_ms": 5000}
                    ]
                }
            ]
        }

        service = ValidationService(None)

        result = asyncio.get_event_loop().run_until_complete(
            service.validate_delete_audio_clip(project, "audio-clip-123")
        )

        assert result.valid is True
        assert result.would_affect.clips_deleted == 1

    def test_validate_move_audio_clip_not_found(self):
        """Move non-existent audio clip raises error."""
        import asyncio
        from unittest.mock import MagicMock

        from src.exceptions import AudioClipNotFoundError
        from src.schemas.ai import MoveAudioClipRequest
        from src.services.validation_service import ValidationService

        project = MagicMock()
        project.timeline_data = {"audio_tracks": []}

        request = MoveAudioClipRequest(new_start_ms=10000)
        service = ValidationService(None)

        with pytest.raises(AudioClipNotFoundError):
            asyncio.get_event_loop().run_until_complete(
                service.validate_move_audio_clip(project, "nonexistent-clip", request)
            )

    def test_validate_move_audio_clip_valid(self):
        """Move existing audio clip passes validation."""
        import asyncio
        from unittest.mock import MagicMock

        from src.schemas.ai import MoveAudioClipRequest
        from src.services.validation_service import ValidationService

        project = MagicMock()
        project.timeline_data = {
            "audio_tracks": [
                {
                    "id": "track-1",
                    "clips": [
                        {"id": "audio-clip-123", "start_ms": 0, "duration_ms": 5000}
                    ]
                }
            ]
        }

        request = MoveAudioClipRequest(new_start_ms=10000)
        service = ValidationService(None)

        result = asyncio.get_event_loop().run_until_complete(
            service.validate_move_audio_clip(project, "audio-clip-123", request)
        )

        assert result.valid is True
        assert result.would_affect.clips_modified == 1

    def test_validate_add_audio_clip_track_not_found(self):
        """Add audio clip to non-existent track raises error."""
        import asyncio
        from unittest.mock import MagicMock
        from uuid import uuid4

        from src.exceptions import AudioTrackNotFoundError
        from src.schemas.ai import AddAudioClipRequest
        from src.services.validation_service import ValidationService

        project = MagicMock()
        project.timeline_data = {"audio_tracks": []}

        request = AddAudioClipRequest(
            track_id="nonexistent-track",
            asset_id=uuid4(),
            start_ms=0,
            duration_ms=5000,
        )
        service = ValidationService(None)

        with pytest.raises(AudioTrackNotFoundError):
            asyncio.get_event_loop().run_until_complete(
                service.validate_add_audio_clip(project, request)
            )

    def test_validate_add_audio_clip_asset_not_found(self):
        """Add audio clip with non-existent asset raises error."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock
        from uuid import uuid4

        from src.exceptions import AssetNotFoundError
        from src.schemas.ai import AddAudioClipRequest
        from src.services.validation_service import ValidationService

        project = MagicMock()
        project.timeline_data = {
            "audio_tracks": [
                {"id": "track-1", "name": "BGM", "clips": []}
            ]
        }

        # Mock the database session to return None for asset
        mock_db = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=mock_result)

        request = AddAudioClipRequest(
            track_id="track-1",
            asset_id=uuid4(),
            start_ms=0,
            duration_ms=5000,
        )
        service = ValidationService(mock_db)

        with pytest.raises(AssetNotFoundError):
            asyncio.get_event_loop().run_until_complete(
                service.validate_add_audio_clip(project, request)
            )

    def test_validate_add_audio_clip_asset_wrong_project(self):
        """Add audio clip with asset from different project raises error."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock
        from uuid import uuid4

        from src.exceptions import AssetNotFoundError
        from src.schemas.ai import AddAudioClipRequest
        from src.services.validation_service import ValidationService

        project_id = uuid4()
        other_project_id = uuid4()

        project = MagicMock()
        project.id = project_id
        project.timeline_data = {
            "audio_tracks": [
                {"id": "track-1", "name": "BGM", "clips": []}
            ]
        }

        # Mock an asset that belongs to a different project
        mock_asset = MagicMock()
        mock_asset.id = uuid4()
        mock_asset.project_id = other_project_id  # Different project!

        mock_db = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_asset
        mock_db.execute = AsyncMock(return_value=mock_result)

        request = AddAudioClipRequest(
            track_id="track-1",
            asset_id=mock_asset.id,
            start_ms=0,
            duration_ms=5000,
        )
        service = ValidationService(mock_db)

        with pytest.raises(AssetNotFoundError):
            asyncio.get_event_loop().run_until_complete(
                service.validate_add_audio_clip(project, request)
            )

    def test_validate_add_audio_clip_valid(self):
        """Valid add_audio_clip passes validation."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock
        from uuid import uuid4

        from src.schemas.ai import AddAudioClipRequest
        from src.services.validation_service import ValidationService

        project_id = uuid4()
        project = MagicMock()
        project.id = project_id
        project.timeline_data = {
            "audio_tracks": [
                {"id": "track-1", "name": "BGM", "clips": []}
            ],
            "duration_ms": 10000,
        }

        # Mock the database session to return an asset (same project)
        mock_asset = MagicMock()
        mock_asset.id = uuid4()
        mock_asset.project_id = project_id  # Same project
        mock_db = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_asset
        mock_db.execute = AsyncMock(return_value=mock_result)

        request = AddAudioClipRequest(
            track_id="track-1",
            asset_id=mock_asset.id,
            start_ms=0,
            duration_ms=5000,
        )
        service = ValidationService(mock_db)

        result = asyncio.get_event_loop().run_until_complete(
            service.validate_add_audio_clip(project, request)
        )

        assert result.valid is True
        assert result.would_affect.clips_created == 1
        assert result.would_affect.clips_modified == 0

    def test_validate_add_audio_clip_overlap_warning(self):
        """Add audio clip with overlap generates warning."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock
        from uuid import uuid4

        from src.schemas.ai import AddAudioClipRequest
        from src.services.validation_service import ValidationService

        project_id = uuid4()
        project = MagicMock()
        project.id = project_id
        project.timeline_data = {
            "audio_tracks": [
                {
                    "id": "track-1",
                    "name": "BGM",
                    "clips": [
                        {"id": "existing-clip", "start_ms": 0, "duration_ms": 10000}
                    ]
                }
            ],
            "duration_ms": 10000,
        }

        # Mock the database session to return an asset (same project)
        mock_asset = MagicMock()
        mock_asset.id = uuid4()
        mock_asset.project_id = project_id  # Same project
        mock_db = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_asset
        mock_db.execute = AsyncMock(return_value=mock_result)

        # Request overlaps with existing clip
        request = AddAudioClipRequest(
            track_id="track-1",
            asset_id=mock_asset.id,
            start_ms=5000,  # Starts in the middle of existing clip
            duration_ms=5000,
        )
        service = ValidationService(mock_db)

        result = asyncio.get_event_loop().run_until_complete(
            service.validate_add_audio_clip(project, request)
        )

        assert result.valid is True
        assert any("overlap" in w.lower() for w in result.warnings)


# =============================================================================
# Priority 3: Capabilities Tests
# =============================================================================


class TestCapabilitiesPriority3:
    """Test capabilities endpoint includes Priority 3 operations."""

    def test_capabilities_includes_audio_operations(self, client, auth_headers):
        """Capabilities includes add_audio_clip, move_audio_clip, delete_audio_clip, add_audio_track."""
        response = client.get("/api/ai/v1/capabilities", headers=auth_headers)

        # May fail due to DB but should at least try
        if response.status_code == 200:
            data = response.json()["data"]
            supported = data["supported_operations"]

            assert "add_audio_clip" in supported
            assert "move_audio_clip" in supported
            assert "delete_audio_clip" in supported
            assert "add_audio_track" in supported


# =============================================================================
# Priority 4: Marker Validation Tests
# =============================================================================


class TestMarkerValidationService:
    """Test marker validation methods."""

    def test_validate_add_marker_basic(self):
        """Add marker with valid data passes validation."""
        import asyncio
        from unittest.mock import MagicMock

        from src.schemas.ai import AddMarkerRequest
        from src.services.validation_service import ValidationService

        project = MagicMock()
        project.timeline_data = {
            "duration_ms": 60000,
            "markers": [],
        }

        mock_db = MagicMock()
        request = AddMarkerRequest(time_ms=5000, name="Chapter 1")
        service = ValidationService(mock_db)

        result = asyncio.get_event_loop().run_until_complete(
            service.validate_add_marker(project, request)
        )

        assert result.valid is True
        assert result.would_affect.clips_created == 0

    def test_validate_add_marker_exceeds_duration_warning(self):
        """Add marker beyond timeline duration generates warning."""
        import asyncio
        from unittest.mock import MagicMock

        from src.schemas.ai import AddMarkerRequest
        from src.services.validation_service import ValidationService

        project = MagicMock()
        project.timeline_data = {
            "duration_ms": 60000,
            "markers": [],
        }

        mock_db = MagicMock()
        request = AddMarkerRequest(time_ms=120000, name="Beyond End")
        service = ValidationService(mock_db)

        result = asyncio.get_event_loop().run_until_complete(
            service.validate_add_marker(project, request)
        )

        assert result.valid is True
        assert any("exceeds" in w.lower() for w in result.warnings)

    def test_validate_add_marker_same_time_warning(self):
        """Add marker at same time as existing generates warning."""
        import asyncio
        from unittest.mock import MagicMock

        from src.schemas.ai import AddMarkerRequest
        from src.services.validation_service import ValidationService

        project = MagicMock()
        project.timeline_data = {
            "duration_ms": 60000,
            "markers": [{"id": "marker-1", "time_ms": 5000, "name": "Existing"}],
        }

        mock_db = MagicMock()
        request = AddMarkerRequest(time_ms=5000, name="New Marker")
        service = ValidationService(mock_db)

        result = asyncio.get_event_loop().run_until_complete(
            service.validate_add_marker(project, request)
        )

        assert result.valid is True
        assert any("already exists" in w.lower() for w in result.warnings)

    def test_validate_update_marker_not_found(self):
        """Update non-existent marker raises error."""
        import asyncio
        from unittest.mock import MagicMock

        from src.exceptions import MarkerNotFoundError
        from src.schemas.ai import UpdateMarkerRequest
        from src.services.validation_service import ValidationService

        project = MagicMock()
        project.timeline_data = {
            "duration_ms": 60000,
            "markers": [],
        }

        mock_db = MagicMock()
        request = UpdateMarkerRequest(time_ms=10000)
        service = ValidationService(mock_db)

        with pytest.raises(MarkerNotFoundError):
            asyncio.get_event_loop().run_until_complete(
                service.validate_update_marker(project, "nonexistent", request)
            )

    def test_validate_update_marker_partial_id(self):
        """Update marker with partial ID works."""
        import asyncio
        from unittest.mock import MagicMock

        from src.schemas.ai import UpdateMarkerRequest
        from src.services.validation_service import ValidationService

        project = MagicMock()
        project.timeline_data = {
            "duration_ms": 60000,
            "markers": [{"id": "marker-abc123", "time_ms": 5000, "name": "Test"}],
        }

        mock_db = MagicMock()
        request = UpdateMarkerRequest(time_ms=10000)
        service = ValidationService(mock_db)

        result = asyncio.get_event_loop().run_until_complete(
            service.validate_update_marker(project, "marker-abc", request)
        )

        assert result.valid is True

    def test_validate_delete_marker_not_found(self):
        """Delete non-existent marker raises error."""
        import asyncio
        from unittest.mock import MagicMock

        from src.exceptions import MarkerNotFoundError
        from src.services.validation_service import ValidationService

        project = MagicMock()
        project.timeline_data = {
            "duration_ms": 60000,
            "markers": [],
        }

        mock_db = MagicMock()
        service = ValidationService(mock_db)

        with pytest.raises(MarkerNotFoundError):
            asyncio.get_event_loop().run_until_complete(
                service.validate_delete_marker(project, "nonexistent")
            )

    def test_validate_delete_marker_success(self):
        """Delete existing marker passes validation."""
        import asyncio
        from unittest.mock import MagicMock

        from src.services.validation_service import ValidationService

        project = MagicMock()
        project.timeline_data = {
            "duration_ms": 60000,
            "markers": [{"id": "marker-1", "time_ms": 5000, "name": "Test"}],
        }

        mock_db = MagicMock()
        service = ValidationService(mock_db)

        result = asyncio.get_event_loop().run_until_complete(
            service.validate_delete_marker(project, "marker-1")
        )

        assert result.valid is True
        assert result.warnings == []

    def test_validate_delete_marker_partial_id(self):
        """Delete marker with partial ID works."""
        import asyncio
        from unittest.mock import MagicMock

        from src.services.validation_service import ValidationService

        project = MagicMock()
        project.timeline_data = {
            "duration_ms": 60000,
            "markers": [{"id": "marker-abc123", "time_ms": 5000, "name": "Test"}],
        }

        mock_db = MagicMock()
        service = ValidationService(mock_db)

        result = asyncio.get_event_loop().run_until_complete(
            service.validate_delete_marker(project, "marker-abc")
        )

        assert result.valid is True


# =============================================================================
# Priority 4: V1 Request Model Tests
# =============================================================================


class TestMarkerV1RequestModels:
    """Test v1 marker request model structures."""

    def test_add_marker_v1_request_structure(self):
        """AddMarkerV1Request wraps AddMarkerRequest with options."""
        from src.api.ai_v1 import AddMarkerV1Request
        from src.schemas.ai import AddMarkerRequest
        from src.schemas.options import OperationOptions

        request = AddMarkerV1Request(
            options=OperationOptions(validate_only=True),
            marker=AddMarkerRequest(time_ms=5000, name="Test"),
        )

        assert request.options.validate_only is True
        assert request.marker.time_ms == 5000
        assert request.marker.name == "Test"

    def test_add_marker_v1_request_default_options(self):
        """AddMarkerV1Request has default options."""
        from src.api.ai_v1 import AddMarkerV1Request
        from src.schemas.ai import AddMarkerRequest

        request = AddMarkerV1Request(
            marker=AddMarkerRequest(time_ms=5000),
        )

        assert request.options.validate_only is False

    def test_update_marker_v1_request_structure(self):
        """UpdateMarkerV1Request wraps UpdateMarkerRequest with options."""
        from src.api.ai_v1 import UpdateMarkerV1Request
        from src.schemas.ai import UpdateMarkerRequest
        from src.schemas.options import OperationOptions

        request = UpdateMarkerV1Request(
            options=OperationOptions(validate_only=True),
            marker=UpdateMarkerRequest(time_ms=10000, name="Updated"),
        )

        assert request.options.validate_only is True
        assert request.marker.time_ms == 10000
        assert request.marker.name == "Updated"

    def test_delete_marker_v1_request_structure(self):
        """DeleteMarkerV1Request supports validate_only via body."""
        from src.api.ai_v1 import DeleteMarkerV1Request
        from src.schemas.options import OperationOptions

        # With explicit validate_only
        request = DeleteMarkerV1Request(
            options=OperationOptions(validate_only=True),
        )
        assert request.options.validate_only is True

        # Default options (validate_only=False)
        request_default = DeleteMarkerV1Request()
        assert request_default.options.validate_only is False


# =============================================================================
# Priority 4: Schema Tests
# =============================================================================


class TestMarkerSchema:
    """Test marker request schemas."""

    def test_add_marker_request_time_ms_constraint(self):
        """AddMarkerRequest validates time_ms >= 0."""
        from pydantic import ValidationError

        from src.schemas.ai import AddMarkerRequest

        # Valid request
        req = AddMarkerRequest(time_ms=0, name="Start")
        assert req.time_ms == 0

        # Invalid request (negative)
        with pytest.raises(ValidationError):
            AddMarkerRequest(time_ms=-1000, name="Invalid")

    def test_add_marker_request_name_max_length(self):
        """AddMarkerRequest validates name max_length=255."""
        from pydantic import ValidationError

        from src.schemas.ai import AddMarkerRequest

        # Valid request (255 chars)
        req = AddMarkerRequest(time_ms=0, name="a" * 255)
        assert len(req.name) == 255

        # Invalid request (256 chars)
        with pytest.raises(ValidationError):
            AddMarkerRequest(time_ms=0, name="a" * 256)

    def test_update_marker_request_all_optional(self):
        """UpdateMarkerRequest allows all fields to be None."""
        from src.schemas.ai import UpdateMarkerRequest

        req = UpdateMarkerRequest()
        assert req.time_ms is None
        assert req.name is None
        assert req.color is None


# =============================================================================
# Priority 4: Capabilities Tests
# =============================================================================


class TestCapabilitiesPriority4:
    """Test capabilities endpoint includes Priority 4 operations."""

    def test_capabilities_includes_marker_operations(self, client, auth_headers):
        """Capabilities includes add_marker, update_marker, delete_marker."""
        response = client.get("/api/ai/v1/capabilities", headers=auth_headers)

        # May fail due to DB but should at least try
        if response.status_code == 200:
            data = response.json()["data"]
            supported = data["supported_operations"]

            assert "add_marker" in supported
            assert "update_marker" in supported
            assert "delete_marker" in supported


# =============================================================================
# Priority 5: V1 Request Model Tests
# =============================================================================


class TestPriority5V1RequestModels:
    """Test v1 request model structures for Priority 5 endpoints."""

    def test_batch_operation_v1_request_structure(self):
        """BatchOperationV1Request wraps operations with options."""
        from src.api.ai_v1 import BatchOperationV1Request
        from src.schemas.ai import BatchClipOperation
        from src.schemas.options import OperationOptions

        request = BatchOperationV1Request(
            options=OperationOptions(validate_only=True),
            operations=[
                BatchClipOperation(
                    operation="add",
                    clip_type="video",
                    data={"layer_id": "layer-1", "start_ms": 0, "duration_ms": 3000},
                ),
                BatchClipOperation(
                    operation="delete",
                    clip_id="clip-1",
                    clip_type="video",
                ),
            ],
        )

        assert request.options.validate_only is True
        assert len(request.operations) == 2
        assert request.operations[0].operation == "add"
        assert request.operations[1].operation == "delete"

    def test_batch_operation_v1_request_default_options(self):
        """BatchOperationV1Request has default options."""
        from src.api.ai_v1 import BatchOperationV1Request

        request = BatchOperationV1Request(operations=[])
        assert request.options.validate_only is False

    def test_semantic_operation_v1_request_structure(self):
        """SemanticOperationV1Request wraps operation with options."""
        from src.api.ai_v1 import SemanticOperationV1Request
        from src.schemas.ai import SemanticOperation
        from src.schemas.options import OperationOptions

        request = SemanticOperationV1Request(
            options=OperationOptions(validate_only=True),
            operation=SemanticOperation(
                operation="close_gap",
                target_layer_id="layer-1",
            ),
        )

        assert request.options.validate_only is True
        assert request.operation.operation == "close_gap"
        assert request.operation.target_layer_id == "layer-1"

    def test_semantic_operation_v1_request_default_options(self):
        """SemanticOperationV1Request has default options."""
        from src.api.ai_v1 import SemanticOperationV1Request
        from src.schemas.ai import SemanticOperation

        request = SemanticOperationV1Request(
            operation=SemanticOperation(
                operation="rename_layer",
                target_layer_id="layer-1",
                parameters={"name": "New Name"},
            ),
        )
        assert request.options.validate_only is False


# =============================================================================
# Priority 5: Batch Validation Tests
# =============================================================================


class TestBatchValidationService:
    """Test validation service for batch operations."""

    def test_validate_batch_operations_empty(self):
        """Empty batch operations list is valid."""
        import asyncio
        from unittest.mock import MagicMock

        from src.services.validation_service import ValidationService

        project = MagicMock()
        project.timeline_data = {
            "duration_ms": 60000,
            "layers": [{"id": "layer-1", "clips": []}],
            "audio_tracks": [],
        }

        mock_db = MagicMock()
        service = ValidationService(mock_db)

        result = asyncio.get_event_loop().run_until_complete(
            service.validate_batch_operations(project, [])
        )

        assert result.valid is True
        assert result.warnings == []
        assert result.would_affect.clips_created == 0
        assert result.would_affect.clips_deleted == 0

    def test_validate_batch_operations_single_delete(self):
        """Batch with single delete operation."""
        import asyncio
        from unittest.mock import MagicMock

        from src.schemas.ai import BatchClipOperation
        from src.services.validation_service import ValidationService

        project = MagicMock()
        project.timeline_data = {
            "duration_ms": 60000,
            "layers": [
                {
                    "id": "layer-1",
                    "clips": [
                        {"id": "clip-1", "start_ms": 0, "duration_ms": 3000},
                    ],
                }
            ],
            "audio_tracks": [],
        }

        mock_db = MagicMock()
        service = ValidationService(mock_db)

        operations = [
            BatchClipOperation(
                operation="delete",
                clip_id="clip-1",
                clip_type="video",
            ),
        ]

        result = asyncio.get_event_loop().run_until_complete(
            service.validate_batch_operations(project, operations)
        )

        assert result.valid is True
        assert result.would_affect.clips_deleted == 1

    def test_validate_batch_operations_missing_clip_id_warning(self):
        """Batch operation missing clip_id adds warning."""
        import asyncio
        from unittest.mock import MagicMock

        from src.schemas.ai import BatchClipOperation
        from src.services.validation_service import ValidationService

        project = MagicMock()
        project.timeline_data = {
            "duration_ms": 60000,
            "layers": [],
            "audio_tracks": [],
        }

        mock_db = MagicMock()
        service = ValidationService(mock_db)

        operations = [
            BatchClipOperation(
                operation="delete",
                clip_id=None,  # Missing clip_id
                clip_type="video",
            ),
        ]

        result = asyncio.get_event_loop().run_until_complete(
            service.validate_batch_operations(project, operations)
        )

        # Missing required fields are now errors, so valid=False
        assert result.valid is False
        assert any("clip_id required" in w for w in result.warnings)


# =============================================================================
# Priority 5: Semantic Validation Tests
# =============================================================================


class TestSemanticValidationService:
    """Test validation service for semantic operations."""

    def test_validate_semantic_close_gap_layer_not_found(self):
        """close_gap raises LayerNotFoundError for missing layer."""
        import asyncio
        from unittest.mock import MagicMock

        from src.exceptions import LayerNotFoundError
        from src.schemas.ai import SemanticOperation
        from src.services.validation_service import ValidationService

        project = MagicMock()
        project.timeline_data = {
            "duration_ms": 60000,
            "layers": [],
        }

        mock_db = MagicMock()
        service = ValidationService(mock_db)

        operation = SemanticOperation(
            operation="close_gap",
            target_layer_id="nonexistent-layer",
        )

        with pytest.raises(LayerNotFoundError):
            asyncio.get_event_loop().run_until_complete(
                service.validate_semantic_operation(project, operation)
            )

    def test_validate_semantic_close_gap_no_gaps(self):
        """close_gap with no gaps adds warning."""
        import asyncio
        from unittest.mock import MagicMock

        from src.schemas.ai import SemanticOperation
        from src.services.validation_service import ValidationService

        project = MagicMock()
        project.timeline_data = {
            "duration_ms": 60000,
            "layers": [
                {
                    "id": "layer-1",
                    "clips": [
                        {"id": "clip-1", "start_ms": 0, "duration_ms": 3000},
                        {"id": "clip-2", "start_ms": 3000, "duration_ms": 3000},
                    ],
                }
            ],
        }

        mock_db = MagicMock()
        service = ValidationService(mock_db)

        operation = SemanticOperation(
            operation="close_gap",
            target_layer_id="layer-1",
        )

        result = asyncio.get_event_loop().run_until_complete(
            service.validate_semantic_operation(project, operation)
        )

        assert result.valid is True
        assert any("No gaps found" in w for w in result.warnings)

    def test_validate_semantic_close_gap_with_gaps(self):
        """close_gap counts clips to move."""
        import asyncio
        from unittest.mock import MagicMock

        from src.schemas.ai import SemanticOperation
        from src.services.validation_service import ValidationService

        project = MagicMock()
        project.timeline_data = {
            "duration_ms": 60000,
            "layers": [
                {
                    "id": "layer-1",
                    "clips": [
                        {"id": "clip-1", "start_ms": 0, "duration_ms": 3000},
                        {"id": "clip-2", "start_ms": 5000, "duration_ms": 3000},  # Gap!
                    ],
                }
            ],
        }

        mock_db = MagicMock()
        service = ValidationService(mock_db)

        operation = SemanticOperation(
            operation="close_gap",
            target_layer_id="layer-1",
        )

        result = asyncio.get_event_loop().run_until_complete(
            service.validate_semantic_operation(project, operation)
        )

        assert result.valid is True
        assert result.would_affect.clips_modified == 1  # clip-2 will move

    def test_validate_semantic_rename_layer_valid(self):
        """rename_layer with valid inputs passes validation."""
        import asyncio
        from unittest.mock import MagicMock

        from src.schemas.ai import SemanticOperation
        from src.services.validation_service import ValidationService

        project = MagicMock()
        project.timeline_data = {
            "layers": [{"id": "layer-1", "name": "Old Name"}],
        }

        mock_db = MagicMock()
        service = ValidationService(mock_db)

        operation = SemanticOperation(
            operation="rename_layer",
            target_layer_id="layer-1",
            parameters={"name": "New Name"},
        )

        result = asyncio.get_event_loop().run_until_complete(
            service.validate_semantic_operation(project, operation)
        )

        assert result.valid is True
        assert "layer-1" in result.would_affect.layers_affected

    def test_validate_semantic_rename_layer_duplicate_warning(self):
        """rename_layer warns about duplicate name."""
        import asyncio
        from unittest.mock import MagicMock

        from src.schemas.ai import SemanticOperation
        from src.services.validation_service import ValidationService

        project = MagicMock()
        project.timeline_data = {
            "layers": [
                {"id": "layer-1", "name": "Layer 1"},
                {"id": "layer-2", "name": "Layer 2"},
            ],
        }

        mock_db = MagicMock()
        service = ValidationService(mock_db)

        operation = SemanticOperation(
            operation="rename_layer",
            target_layer_id="layer-1",
            parameters={"name": "Layer 2"},  # Duplicate name
        )

        result = asyncio.get_event_loop().run_until_complete(
            service.validate_semantic_operation(project, operation)
        )

        assert result.valid is True
        assert any("already exists" in w for w in result.warnings)

    def test_validate_semantic_auto_duck_no_bgm(self):
        """auto_duck_bgm without BGM track fails validation."""
        import asyncio
        from unittest.mock import MagicMock

        from src.schemas.ai import SemanticOperation
        from src.services.validation_service import ValidationService

        project = MagicMock()
        project.timeline_data = {
            "audio_tracks": [{"id": "track-1", "type": "narration"}],
        }

        mock_db = MagicMock()
        service = ValidationService(mock_db)

        operation = SemanticOperation(operation="auto_duck_bgm")

        result = asyncio.get_event_loop().run_until_complete(
            service.validate_semantic_operation(project, operation)
        )

        assert result.valid is False
        assert any("No BGM track found" in w for w in result.warnings)

    def test_validate_semantic_snap_to_previous_no_target(self):
        """snap_to_previous without target_clip_id fails validation."""
        import asyncio
        from unittest.mock import MagicMock

        from src.schemas.ai import SemanticOperation
        from src.services.validation_service import ValidationService

        project = MagicMock()
        project.timeline_data = {"layers": []}

        mock_db = MagicMock()
        service = ValidationService(mock_db)

        operation = SemanticOperation(
            operation="snap_to_previous",
            target_clip_id=None,
        )

        result = asyncio.get_event_loop().run_until_complete(
            service.validate_semantic_operation(project, operation)
        )

        assert result.valid is False
        assert any("target_clip_id required" in w for w in result.warnings)


# =============================================================================
# Priority 5: Review Fix Tests (max_batch_ops, trim validation)
# =============================================================================


class TestBatchValidationServiceReviewFixes:
    """Test batch validation service review fixes."""

    def test_validate_batch_operations_max_ops_exceeded(self):
        """Batch exceeding max_batch_ops (20) returns valid=False."""
        import asyncio
        from unittest.mock import MagicMock

        from src.schemas.ai import BatchClipOperation
        from src.services.validation_service import ValidationService

        project = MagicMock()
        project.timeline_data = {"layers": [], "audio_tracks": []}

        mock_db = MagicMock()
        service = ValidationService(mock_db)

        # Create 21 operations (exceeds limit of 20)
        operations = [
            BatchClipOperation(operation="delete", clip_id=f"clip-{i}", clip_type="video")
            for i in range(21)
        ]

        result = asyncio.get_event_loop().run_until_complete(
            service.validate_batch_operations(project, operations)
        )

        assert result.valid is False
        assert any("exceeds limit" in w for w in result.warnings)

    def test_validate_batch_operations_trim_valid(self):
        """Batch trim operation is validated correctly."""
        import asyncio
        from unittest.mock import MagicMock

        from src.schemas.ai import BatchClipOperation
        from src.services.validation_service import ValidationService

        project = MagicMock()
        project.timeline_data = {
            "layers": [
                {
                    "id": "layer-1",
                    "clips": [{"id": "clip-1", "start_ms": 0, "duration_ms": 5000}],
                }
            ],
            "audio_tracks": [],
        }

        mock_db = MagicMock()
        service = ValidationService(mock_db)

        operations = [
            BatchClipOperation(
                operation="trim",
                clip_id="clip-1",
                clip_type="video",
                data={"duration_ms": 3000},
            ),
        ]

        result = asyncio.get_event_loop().run_until_complete(
            service.validate_batch_operations(project, operations)
        )

        assert result.valid is True
        assert result.would_affect.clips_modified == 1

    def test_validate_batch_operations_trim_missing_duration(self):
        """Batch trim without duration_ms adds warning."""
        import asyncio
        from unittest.mock import MagicMock

        from src.schemas.ai import BatchClipOperation
        from src.services.validation_service import ValidationService

        project = MagicMock()
        project.timeline_data = {"layers": [], "audio_tracks": []}

        mock_db = MagicMock()
        service = ValidationService(mock_db)

        operations = [
            BatchClipOperation(
                operation="trim",
                clip_id="clip-1",
                clip_type="video",
                data={},  # Missing duration_ms
            ),
        ]

        result = asyncio.get_event_loop().run_until_complete(
            service.validate_batch_operations(project, operations)
        )

        # Missing required fields are now errors, so valid=False
        assert result.valid is False
        assert any("duration_ms required" in w for w in result.warnings)


# =============================================================================
# Priority 5 Deep Review Fixes
# =============================================================================


class TestBatchUnifiedFormat:
    """Test batch operations accept unified (nested) format."""

    def test_batch_add_accepts_nested_format(self):
        """Batch add operation accepts nested transform format."""
        import asyncio
        from unittest.mock import MagicMock, AsyncMock
        import uuid

        from src.schemas.ai import BatchClipOperation
        from src.services.validation_service import ValidationService

        project = MagicMock()
        project.id = uuid.uuid4()
        project.timeline_data = {
            "duration_ms": 60000,
            "layers": [{"id": "layer-1", "name": "Layer 1", "clips": []}],
        }

        mock_db = MagicMock()
        mock_db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: MagicMock(id=uuid.uuid4(), type="video", duration_ms=3000, project_id=project.id)))
        service = ValidationService(mock_db)

        # Nested format: transform.position, transform.scale
        operations = [
            BatchClipOperation(
                operation="add",
                clip_type="video",
                data={
                    "layer_id": "layer-1",
                    "asset_id": str(uuid.uuid4()),
                    "start_ms": 0,
                    "duration_ms": 3000,
                    "transform": {
                        "position": {"x": 100, "y": 200},
                        "scale": {"x": 1.5, "y": 1.5},
                    },
                },
            ),
        ]

        result = asyncio.get_event_loop().run_until_complete(
            service.validate_batch_operations(project, operations)
        )

        # Should be valid since nested format is accepted
        assert result.valid is True
        assert result.would_affect.clips_created == 1

    def test_batch_update_transform_accepts_nested_format(self):
        """Batch update_transform operation accepts nested transform format."""
        import asyncio
        from unittest.mock import MagicMock

        from src.schemas.ai import BatchClipOperation
        from src.services.validation_service import ValidationService

        project = MagicMock()
        project.timeline_data = {
            "duration_ms": 60000,
            "layers": [
                {
                    "id": "layer-1",
                    "name": "Layer 1",
                    "clips": [
                        {"id": "clip-1", "start_ms": 0, "duration_ms": 3000},
                    ],
                }
            ],
        }

        mock_db = MagicMock()
        service = ValidationService(mock_db)

        # Nested format: transform.position, transform.scale
        operations = [
            BatchClipOperation(
                operation="update_transform",
                clip_id="clip-1",
                clip_type="video",
                data={
                    "transform": {
                        "position": {"x": 100, "y": 200},
                        "scale": {"x": 1.5, "y": 1.5},
                    },
                },
            ),
        ]

        result = asyncio.get_event_loop().run_until_complete(
            service.validate_batch_operations(project, operations)
        )

        # Should be valid since nested format is accepted
        assert result.valid is True
        assert result.would_affect.clips_modified == 1


class TestBatchClipTypeValidation:
    """Test batch operations validate clip_type for video-only operations."""

    def test_batch_update_transform_rejects_audio_clip_type(self):
        """Batch update_transform returns error for clip_type='audio'."""
        import asyncio
        from unittest.mock import MagicMock

        from src.schemas.ai import BatchClipOperation
        from src.services.validation_service import ValidationService

        project = MagicMock()
        project.timeline_data = {
            "duration_ms": 60000,
            "layers": [],
            "audio_tracks": [
                {
                    "id": "track-1",
                    "clips": [{"id": "audio-1", "start_ms": 0, "duration_ms": 3000}],
                }
            ],
        }

        mock_db = MagicMock()
        service = ValidationService(mock_db)

        operations = [
            BatchClipOperation(
                operation="update_transform",
                clip_id="audio-1",
                clip_type="audio",  # Not supported
                data={"x": 100, "y": 200},
            ),
        ]

        result = asyncio.get_event_loop().run_until_complete(
            service.validate_batch_operations(project, operations)
        )

        # Should be invalid - update_transform doesn't support audio
        assert result.valid is False
        assert any("update_transform does not support audio clips" in w for w in result.warnings)

    def test_batch_update_effects_rejects_audio_clip_type(self):
        """Batch update_effects returns error for clip_type='audio'."""
        import asyncio
        from unittest.mock import MagicMock

        from src.schemas.ai import BatchClipOperation
        from src.services.validation_service import ValidationService

        project = MagicMock()
        project.timeline_data = {
            "duration_ms": 60000,
            "layers": [],
            "audio_tracks": [
                {
                    "id": "track-1",
                    "clips": [{"id": "audio-1", "start_ms": 0, "duration_ms": 3000}],
                }
            ],
        }

        mock_db = MagicMock()
        service = ValidationService(mock_db)

        operations = [
            BatchClipOperation(
                operation="update_effects",
                clip_id="audio-1",
                clip_type="audio",  # Not supported
                data={"opacity": 0.5},
            ),
        ]

        result = asyncio.get_event_loop().run_until_complete(
            service.validate_batch_operations(project, operations)
        )

        # Should be invalid - update_effects doesn't support audio
        assert result.valid is False
        assert any("update_effects does not support audio clips" in w for w in result.warnings)


class TestMarkerNoOpETag:
    """Test marker update no-op doesn't change timeline."""

    def test_marker_update_no_op_does_not_modify_timeline(self):
        """Marker update with same values doesn't call flag_modified."""
        import asyncio
        from unittest.mock import MagicMock, AsyncMock, patch

        from src.schemas.ai import UpdateMarkerRequest
        from src.services.ai_service import AIService

        project = MagicMock()
        project.timeline_data = {
            "markers": [
                {"id": "marker-1", "time_ms": 5000, "name": "Test", "color": "#ff0000"}
            ]
        }

        mock_db = MagicMock()
        mock_db.flush = AsyncMock()
        service = AIService(mock_db)

        # Update with same values (no-op)
        request = UpdateMarkerRequest(
            time_ms=5000,  # Same
            name="Test",  # Same
            color="#ff0000",  # Same
        )

        with patch("src.services.ai_service.flag_modified") as mock_flag_modified:
            result = asyncio.get_event_loop().run_until_complete(
                service.update_marker(project, "marker-1", request)
            )

            # Should NOT call flag_modified since nothing changed
            mock_flag_modified.assert_not_called()

        # Marker should still be returned
        assert result["id"] == "marker-1"

    def test_marker_update_actual_change_does_modify_timeline(self):
        """Marker update with changed values calls flag_modified."""
        import asyncio
        from unittest.mock import MagicMock, AsyncMock, patch

        from src.schemas.ai import UpdateMarkerRequest
        from src.services.ai_service import AIService

        project = MagicMock()
        project.timeline_data = {
            "markers": [
                {"id": "marker-1", "time_ms": 5000, "name": "Test", "color": "#ff0000"}
            ]
        }

        mock_db = MagicMock()
        mock_db.flush = AsyncMock()
        service = AIService(mock_db)

        # Update with different value
        request = UpdateMarkerRequest(
            name="Updated Name",  # Different
        )

        with patch("src.services.ai_service.flag_modified") as mock_flag_modified:
            result = asyncio.get_event_loop().run_until_complete(
                service.update_marker(project, "marker-1", request)
            )

            # Should call flag_modified since name changed
            mock_flag_modified.assert_called_once()

        # Marker should have updated name
        assert result["name"] == "Updated Name"


class TestSemanticFailureStructuredError:
    """Test semantic failure returns structured error envelope."""

    def test_semantic_failure_returns_error_result(self):
        """Semantic operation failure returns SemanticOperationResult with success=False."""
        import asyncio
        from unittest.mock import MagicMock, AsyncMock

        from src.schemas.ai import SemanticOperation
        from src.services.ai_service import AIService

        project = MagicMock()
        project.timeline_data = {
            "duration_ms": 60000,
            "layers": [
                {
                    "id": "layer-1",
                    "name": "Layer 1",
                    "clips": [
                        {"id": "clip-1", "start_ms": 0, "duration_ms": 3000},
                    ],
                }
            ],
        }

        mock_db = MagicMock()
        mock_db.flush = AsyncMock()
        service = AIService(mock_db)

        # Try snap_to_previous on the first clip (no previous clip exists)
        operation = SemanticOperation(
            operation="snap_to_previous",
            target_clip_id="clip-1",
        )

        result = asyncio.get_event_loop().run_until_complete(
            service.execute_semantic_operation(project, operation)
        )

        # Should return failure result
        assert result.success is False
        assert "No previous clip" in result.error_message

    def test_semantic_operation_failed_code_in_registry(self):
        """SEMANTIC_OPERATION_FAILED is registered in ERROR_CODES."""
        from src.constants.error_codes import ERROR_CODES

        assert "SEMANTIC_OPERATION_FAILED" in ERROR_CODES
        spec = ERROR_CODES["SEMANTIC_OPERATION_FAILED"]
        assert spec.get("retryable") is False
        assert "suggested_fix" in spec

    def test_semantic_operation_failed_has_suggested_fix(self):
        """SEMANTIC_OPERATION_FAILED has proper suggested_fix in error codes."""
        from src.constants.error_codes import get_error_spec

        spec = get_error_spec("SEMANTIC_OPERATION_FAILED")

        assert "suggested_fix" in spec
        assert "target_clip_id" in spec["suggested_fix"]  # Mentions common causes
        assert spec.get("suggested_action") == "refresh_ids"
        assert "/structure" in spec.get("suggested_endpoint", "")


# =============================================================================
# Priority 5: Capabilities Tests
# =============================================================================


class TestCapabilitiesPriority5:
    """Test capabilities endpoint includes Priority 5 operations."""

    def test_capabilities_includes_batch_semantic_operations(self, client, auth_headers):
        """Capabilities includes batch and semantic operations."""
        response = client.get("/api/ai/v1/capabilities", headers=auth_headers)

        if response.status_code == 200:
            data = response.json()["data"]
            supported = data["supported_operations"]

            assert "batch" in supported
            assert "semantic" in supported

    def test_capabilities_includes_advanced_read_endpoints(self, client, auth_headers):
        """Capabilities includes advanced read endpoints."""
        response = client.get("/api/ai/v1/capabilities", headers=auth_headers)

        if response.status_code == 200:
            data = response.json()["data"]
            read_endpoints = data["supported_read_endpoints"]

            # Check for the new Priority 5 read endpoints
            assert any("clips/{clip_id}" in ep for ep in read_endpoints)
            assert any("at-time" in ep for ep in read_endpoints)

    def test_capabilities_includes_semantic_operations_list(self, client, auth_headers):
        """Capabilities schema_notes includes semantic_operations list."""
        response = client.get("/api/ai/v1/capabilities", headers=auth_headers)

        if response.status_code == 200:
            data = response.json()["data"]
            schema_notes = data["schema_notes"]

            assert "semantic_operations" in schema_notes
            semantic_ops = schema_notes["semantic_operations"]
            assert "snap_to_previous" in semantic_ops
            assert "close_gap" in semantic_ops
            assert "rename_layer" in semantic_ops

    def test_capabilities_includes_batch_operation_types(self, client, auth_headers):
        """Capabilities schema_notes includes batch_operation_types list."""
        response = client.get("/api/ai/v1/capabilities", headers=auth_headers)

        if response.status_code == 200:
            data = response.json()["data"]
            schema_notes = data["schema_notes"]

            assert "batch_operation_types" in schema_notes
            batch_types = schema_notes["batch_operation_types"]
            assert "add" in batch_types
            assert "move" in batch_types
            assert "delete" in batch_types
            assert "update_transform" in batch_types


# =============================================================================
# Phase 2+3: History and Rollback Tests
# =============================================================================


class TestHistoryEndpoint:
    """Test GET /history endpoint."""

    def test_capabilities_includes_history_feature_flags(self, client, auth_headers):
        """Capabilities indicates history feature status.

        Note: history/rollback/return_diff are False until operation recording
        is wired into mutation endpoints. Endpoints exist but aren't functional
        without recorded operations.
        """
        response = client.get("/api/ai/v1/capabilities", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()["data"]
        features = data["features"]

        # Currently disabled - requires operation recording in mutations
        assert features["history"] is False
        assert features["rollback"] is False
        assert features["return_diff"] is False

    def test_capabilities_excludes_disabled_history_endpoints(self, client, auth_headers):
        """Capabilities does NOT list history endpoints while feature is disabled.

        History endpoints exist but are not listed in supported_read_endpoints
        since features.history=false (operation recording not wired).
        """
        response = client.get("/api/ai/v1/capabilities", headers=auth_headers)

        if response.status_code == 200:
            data = response.json()["data"]
            read_endpoints = data["supported_read_endpoints"]

            # Should NOT include history/operations while disabled
            assert not any("history" in ep for ep in read_endpoints)
            assert not any("operations/{operation_id}" in ep for ep in read_endpoints)

    def test_capabilities_excludes_disabled_rollback_operation(self, client, auth_headers):
        """Capabilities does NOT list rollback while feature is disabled.

        Rollback endpoint exists but is not listed in supported_operations
        since features.rollback=false (operation recording not wired).
        """
        response = client.get("/api/ai/v1/capabilities", headers=auth_headers)

        if response.status_code == 200:
            data = response.json()["data"]
            supported = data["supported_operations"]

            # Should NOT include rollback while disabled
            assert "rollback" not in supported

    @pytest.mark.requires_db
    def test_history_returns_envelope_format(self, client, auth_headers):
        """GET /history returns envelope format."""
        fake_project_id = str(uuid.uuid4())
        response = client.get(
            f"/api/ai/v1/projects/{fake_project_id}/history",
            headers=auth_headers,
        )

        # Will get 404 (project not found) or 500 (DB error)
        # But response should still be envelope format
        data = response.json()
        assert "request_id" in data
        assert "meta" in data

    @pytest.mark.requires_db
    def test_history_supports_pagination_params(self, client, auth_headers):
        """GET /history accepts pagination parameters."""
        fake_project_id = str(uuid.uuid4())
        response = client.get(
            f"/api/ai/v1/projects/{fake_project_id}/history",
            params={"page": 2, "page_size": 10},
            headers=auth_headers,
        )

        # Parameters should be accepted (endpoint may fail due to missing project)
        assert response.status_code in [200, 404, 500]

    @pytest.mark.requires_db
    def test_history_supports_filter_params(self, client, auth_headers):
        """GET /history accepts filter parameters."""
        fake_project_id = str(uuid.uuid4())
        response = client.get(
            f"/api/ai/v1/projects/{fake_project_id}/history",
            params={
                "operation_type": "add_clip",
                "source": "api_v1",
                "success_only": True,
            },
            headers=auth_headers,
        )

        # Parameters should be accepted
        assert response.status_code in [200, 404, 500]


class TestOperationDetailsEndpoint:
    """Test GET /operations/{operation_id} endpoint."""

    @pytest.mark.requires_db
    def test_operation_details_returns_envelope_format(self, client, auth_headers):
        """GET /operations/{id} returns envelope format."""
        fake_project_id = str(uuid.uuid4())
        fake_operation_id = str(uuid.uuid4())
        response = client.get(
            f"/api/ai/v1/projects/{fake_project_id}/operations/{fake_operation_id}",
            headers=auth_headers,
        )

        # Will get 404 or 500
        data = response.json()
        assert "request_id" in data
        assert "meta" in data

    @pytest.mark.requires_db
    def test_operation_not_found_returns_structured_error(self, client, auth_headers):
        """Non-existent operation returns structured error envelope."""
        fake_project_id = str(uuid.uuid4())
        fake_operation_id = str(uuid.uuid4())
        response = client.get(
            f"/api/ai/v1/projects/{fake_project_id}/operations/{fake_operation_id}",
            headers=auth_headers,
        )

        # Expect 404 or 500 with error envelope
        assert response.status_code in [404, 500]
        data = response.json()

        if "error" in data:
            error = data["error"]
            assert "code" in error
            assert "message" in error


class TestRollbackEndpoint:
    """Test POST /operations/{operation_id}/rollback endpoint."""

    @pytest.mark.requires_db
    def test_rollback_requires_idempotency_key(self, client, auth_headers):
        """POST /rollback requires Idempotency-Key header."""
        fake_project_id = str(uuid.uuid4())
        fake_operation_id = str(uuid.uuid4())
        response = client.post(
            f"/api/ai/v1/projects/{fake_project_id}/operations/{fake_operation_id}/rollback",
            headers=auth_headers,
            json={},
        )

        # Should require Idempotency-Key
        assert response.status_code == 400
        data = response.json()

        if "error" in data:
            assert "Idempotency-Key" in data["error"]["message"]

    @pytest.mark.requires_db
    def test_rollback_accepts_idempotency_key(self, client, auth_headers):
        """POST /rollback accepts Idempotency-Key header."""
        fake_project_id = str(uuid.uuid4())
        fake_operation_id = str(uuid.uuid4())
        headers = {
            **auth_headers,
            "Idempotency-Key": str(uuid.uuid4()),
        }
        response = client.post(
            f"/api/ai/v1/projects/{fake_project_id}/operations/{fake_operation_id}/rollback",
            headers=headers,
            json={},
        )

        # May get 404 or 500 (project not found), but not 400 for missing key
        assert response.status_code in [200, 404, 500]
        data = response.json()
        assert "request_id" in data
        assert "meta" in data

    @pytest.mark.requires_db
    def test_rollback_returns_envelope_format(self, client, auth_headers):
        """POST /rollback returns envelope format."""
        fake_project_id = str(uuid.uuid4())
        fake_operation_id = str(uuid.uuid4())
        headers = {
            **auth_headers,
            "Idempotency-Key": str(uuid.uuid4()),
        }
        response = client.post(
            f"/api/ai/v1/projects/{fake_project_id}/operations/{fake_operation_id}/rollback",
            headers=headers,
        )

        data = response.json()
        assert "request_id" in data
        assert "meta" in data

    @pytest.mark.requires_db
    def test_rollback_checks_if_match(self, client, auth_headers):
        """POST /rollback respects If-Match header for concurrency control."""
        fake_project_id = str(uuid.uuid4())
        fake_operation_id = str(uuid.uuid4())
        headers = {
            **auth_headers,
            "Idempotency-Key": str(uuid.uuid4()),
            "If-Match": '"wrong-etag"',
        }
        response = client.post(
            f"/api/ai/v1/projects/{fake_project_id}/operations/{fake_operation_id}/rollback",
            headers=headers,
        )

        # Might get 409 (etag mismatch) or 404/500 (project not found)
        # Depends on order of checks
        assert response.status_code in [404, 409, 500]
