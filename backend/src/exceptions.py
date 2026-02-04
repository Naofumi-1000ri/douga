"""Custom exceptions for the douga backend.

These exceptions integrate with the AI-Friendly API error handling system,
providing machine-readable error codes and suggested recovery actions.
"""

from typing import Any

from src.constants.error_codes import get_error_spec
from src.schemas.envelope import ErrorInfo, ErrorLocation, SuggestedAction


class DougaError(Exception):
    """Base exception for all douga application errors.

    Provides structured error information for AI-Friendly API responses.
    """

    code: str = "INTERNAL_ERROR"
    status_code: int = 500
    message: str = "An unexpected error occurred"

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        status_code: int | None = None,
        location: ErrorLocation | None = None,
        suggested_fix: str | None = None,
    ):
        self.message = message or self.__class__.message
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code
        self.location = location
        self.suggested_fix = suggested_fix
        super().__init__(self.message)

    def to_error_info(self) -> ErrorInfo:
        """Convert exception to ErrorInfo for API response."""
        spec = get_error_spec(self.code)
        retryable = spec.get("retryable", False)

        suggested_actions: list[SuggestedAction] = []
        if "suggested_action" in spec:
            action = SuggestedAction(
                action=spec["suggested_action"],
                endpoint=spec.get("suggested_endpoint"),
                parameters=spec.get("parameters", {}),
            )
            suggested_actions.append(action)

        # Use suggested_fix from spec, or explicit override from exception
        suggested_fix = self.suggested_fix or spec.get("suggested_fix")

        return ErrorInfo(
            code=self.code,
            message=self.message,
            location=self.location,
            retryable=retryable,
            suggested_fix=suggested_fix,
            suggested_actions=suggested_actions,
        )


# =============================================================================
# Resource Not Found Errors (404)
# =============================================================================


class ResourceNotFoundError(DougaError):
    """Base class for resource not found errors."""

    status_code = 404


class ProjectNotFoundError(ResourceNotFoundError):
    """Project not found."""

    code = "PROJECT_NOT_FOUND"
    message = "Project not found"

    def __init__(self, project_id: str | None = None):
        message = f"Project not found: {project_id}" if project_id else self.message
        super().__init__(message)


class ClipNotFoundError(ResourceNotFoundError):
    """Clip not found."""

    code = "CLIP_NOT_FOUND"
    message = "Clip not found"

    def __init__(self, clip_id: str | None = None, layer_id: str | None = None):
        message = f"Clip not found: {clip_id}" if clip_id else self.message
        location = ErrorLocation(clip_id=clip_id, layer_id=layer_id) if clip_id else None
        super().__init__(message, location=location)


class LayerNotFoundError(ResourceNotFoundError):
    """Layer not found."""

    code = "LAYER_NOT_FOUND"
    message = "Layer not found"

    def __init__(self, layer_id: str | None = None):
        message = f"Layer not found: {layer_id}" if layer_id else self.message
        location = ErrorLocation(layer_id=layer_id) if layer_id else None
        super().__init__(message, location=location)


class AssetNotFoundError(ResourceNotFoundError):
    """Asset not found."""

    code = "ASSET_NOT_FOUND"
    message = "Asset not found"

    def __init__(self, asset_id: str | None = None):
        message = f"Asset not found: {asset_id}" if asset_id else self.message
        super().__init__(message)


class AudioTrackNotFoundError(ResourceNotFoundError):
    """Audio track not found."""

    code = "AUDIO_TRACK_NOT_FOUND"
    message = "Audio track not found"

    def __init__(self, track_id: str | None = None):
        message = f"Audio track not found: {track_id}" if track_id else self.message
        super().__init__(message)


class AudioClipNotFoundError(ResourceNotFoundError):
    """Audio clip not found."""

    code = "AUDIO_CLIP_NOT_FOUND"
    message = "Audio clip not found"

    def __init__(self, clip_id: str | None = None, track_id: str | None = None):
        message = f"Audio clip not found: {clip_id}" if clip_id else self.message
        location = ErrorLocation(clip_id=clip_id) if clip_id else None
        super().__init__(message, location=location)


class MarkerNotFoundError(ResourceNotFoundError):
    """Marker not found."""

    code = "MARKER_NOT_FOUND"
    message = "Marker not found"

    def __init__(self, marker_id: str | None = None):
        message = f"Marker not found: {marker_id}" if marker_id else self.message
        super().__init__(message)


class OperationNotFoundError(ResourceNotFoundError):
    """Operation not found."""

    code = "OPERATION_NOT_FOUND"
    message = "Operation not found"

    def __init__(self, operation_id: str | None = None):
        message = f"Operation not found: {operation_id}" if operation_id else self.message
        super().__init__(message)


# =============================================================================
# Validation Errors (400)
# =============================================================================


class ValidationError(DougaError):
    """Base class for validation errors."""

    code = "VALIDATION_ERROR"
    status_code = 400


class InvalidTimeRangeError(ValidationError):
    """Invalid time range specified."""

    code = "INVALID_TIME_RANGE"
    message = "Invalid time range"

    def __init__(
        self,
        message: str | None = None,
        *,
        start_ms: int | None = None,
        end_ms: int | None = None,
        field: str | None = None,
    ):
        msg = message or self.message
        if start_ms is not None and end_ms is not None:
            msg = f"Invalid time range: {start_ms}ms to {end_ms}ms"
        location = ErrorLocation(field=field) if field else None
        super().__init__(msg, location=location)


class OutOfBoundsError(ValidationError):
    """Value is out of allowed bounds."""

    code = "OUT_OF_BOUNDS"
    message = "Value is out of bounds"

    def __init__(
        self,
        message: str | None = None,
        *,
        field: str | None = None,
        value: Any = None,
        min_value: Any = None,
        max_value: Any = None,
    ):
        msg = message or self.message
        if value is not None:
            msg = f"Value {value} is out of bounds"
            if min_value is not None and max_value is not None:
                msg += f" (allowed: {min_value} to {max_value})"
        location = ErrorLocation(field=field) if field else None
        super().__init__(msg, location=location)


class LayerLockedError(ValidationError):
    """Layer is locked and cannot be modified."""

    code = "LAYER_LOCKED"
    message = "Layer is locked"

    def __init__(self, layer_id: str | None = None):
        message = f"Layer is locked: {layer_id}" if layer_id else self.message
        location = ErrorLocation(layer_id=layer_id) if layer_id else None
        super().__init__(message, location=location)


class MissingRequiredFieldError(ValidationError):
    """Required field is missing."""

    code = "MISSING_REQUIRED_FIELD"
    message = "Required field is missing"

    def __init__(self, field: str | None = None):
        message = f"Required field is missing: {field}" if field else self.message
        location = ErrorLocation(field=field) if field else None
        super().__init__(message, location=location)


class InvalidFieldValueError(ValidationError):
    """Field value is invalid."""

    code = "INVALID_FIELD_VALUE"
    message = "Invalid field value"

    def __init__(
        self, message: str | None = None, *, field: str | None = None, value: Any = None
    ):
        msg = message or self.message
        if field and value is not None:
            msg = f"Invalid value for field '{field}': {value}"
        location = ErrorLocation(field=field) if field else None
        super().__init__(msg, location=location)


class DurationTooLongError(ValidationError):
    """Duration exceeds maximum allowed."""

    code = "DURATION_TOO_LONG"
    message = "Duration exceeds maximum allowed"

    def __init__(self, duration_ms: int | None = None, max_ms: int | None = None):
        message = self.message
        if duration_ms is not None and max_ms is not None:
            message = f"Duration {duration_ms}ms exceeds maximum {max_ms}ms"
        super().__init__(message)


class TooManyClipsError(ValidationError):
    """Too many clips in layer."""

    code = "TOO_MANY_CLIPS"
    message = "Too many clips in layer"

    def __init__(self, count: int | None = None, max_count: int | None = None):
        message = self.message
        if count is not None and max_count is not None:
            message = f"Too many clips ({count}) in layer (max: {max_count})"
        super().__init__(message)


class TooManyLayersError(ValidationError):
    """Too many layers in project."""

    code = "TOO_MANY_LAYERS"
    message = "Too many layers in project"

    def __init__(self, count: int | None = None, max_count: int | None = None):
        message = self.message
        if count is not None and max_count is not None:
            message = f"Too many layers ({count}) in project (max: {max_count})"
        super().__init__(message)


# =============================================================================
# Conflict Errors (409)
# =============================================================================


class ConflictError(DougaError):
    """Base class for conflict errors."""

    code = "CONFLICT"
    status_code = 409


class ClipOverlapError(ConflictError):
    """Clips would overlap."""

    code = "CLIP_OVERLAP"
    message = "Clips would overlap"

    def __init__(
        self,
        message: str | None = None,
        *,
        clip_id: str | None = None,
        layer_id: str | None = None,
        conflicting_clip_id: str | None = None,
    ):
        msg = message or self.message
        if conflicting_clip_id:
            msg = f"Clip would overlap with: {conflicting_clip_id}"
        location = ErrorLocation(clip_id=clip_id, layer_id=layer_id) if clip_id else None
        super().__init__(msg, location=location)


class ConcurrentModificationError(ConflictError):
    """Project was modified by another request."""

    code = "CONCURRENT_MODIFICATION"
    message = "Project was modified by another request"


class IdempotencyConflictError(ConflictError):
    """Idempotency key conflict."""

    code = "IDEMPOTENCY_CONFLICT"
    message = "Request with this idempotency key was already processed with different parameters"


# =============================================================================
# Feature/Capability Errors (400/501)
# =============================================================================


class FeatureNotSupportedError(DougaError):
    """Feature is not supported."""

    code = "FEATURE_NOT_SUPPORTED"
    status_code = 400
    message = "Feature is not supported"

    def __init__(self, feature: str | None = None):
        message = f"Feature not supported: {feature}" if feature else self.message
        super().__init__(message)


class OperationNotSupportedError(DougaError):
    """Operation is not supported."""

    code = "OPERATION_NOT_SUPPORTED"
    status_code = 400
    message = "Operation is not supported"

    def __init__(self, operation: str | None = None):
        message = f"Operation not supported: {operation}" if operation else self.message
        super().__init__(message)


# =============================================================================
# Rollback Errors (400/500)
# =============================================================================


class RollbackNotAvailableError(DougaError):
    """Rollback is not available for this operation."""

    code = "ROLLBACK_NOT_AVAILABLE"
    status_code = 400
    message = "Rollback is not available for this operation"

    def __init__(self, operation_id: str | None = None, reason: str | None = None):
        if reason:
            message = f"Rollback not available for operation {operation_id}: {reason}"
        elif operation_id:
            message = f"Rollback not available for operation: {operation_id}"
        else:
            message = self.message
        super().__init__(message)


class OperationAlreadyRolledBackError(DougaError):
    """Operation was already rolled back."""

    code = "OPERATION_ALREADY_ROLLED_BACK"
    status_code = 400
    message = "Operation was already rolled back"

    def __init__(self, operation_id: str | None = None):
        message = (
            f"Operation already rolled back: {operation_id}" if operation_id else self.message
        )
        super().__init__(message)


# =============================================================================
# System Errors (500/503)
# =============================================================================


class InternalError(DougaError):
    """Internal server error."""

    code = "INTERNAL_ERROR"
    status_code = 500
    message = "Internal server error"


class ServiceUnavailableError(DougaError):
    """Service is temporarily unavailable."""

    code = "SERVICE_UNAVAILABLE"
    status_code = 503
    message = "Service is temporarily unavailable"


class DatabaseError(DougaError):
    """Database error."""

    code = "DATABASE_ERROR"
    status_code = 500
    message = "Database error"


class StorageError(DougaError):
    """Storage error."""

    code = "STORAGE_ERROR"
    status_code = 500
    message = "Storage error"
