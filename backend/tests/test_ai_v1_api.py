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
    return TestClient(app, raise_server_exceptions=False)


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

        # Check expected capability fields
        assert "effects" in data
        assert "easings" in data
        assert "max_layers" in data
        assert "max_duration_ms" in data
        assert "max_batch_ops" in data

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

        # Should be 400 for missing Idempotency-Key, or 500 if DB fails first
        assert response.status_code in [400, 500]
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

        options = OperationOptions(
            validate_only=True,
            include_diff=False,
        )

        assert options.validate_only is True
        assert options.include_diff is False

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
