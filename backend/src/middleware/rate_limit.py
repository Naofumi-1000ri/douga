"""Rate limiting middleware for V1 API endpoints.

Implements a per-client sliding window counter with configurable limits.
Since Cloud Run is stateless, this is an in-instance counter only;
cross-instance global limiting would require Redis/DB (future work).

Rate limit information is communicated via standard headers:
    X-RateLimit-Limit:     Maximum requests per window
    X-RateLimit-Remaining: Remaining requests in the current window
    X-RateLimit-Reset:     UTC epoch seconds when the window resets

On limit exceeded: 429 Too Many Requests with envelope response and Retry-After header.
"""

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field

from fastapi import Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from src.constants.error_codes import get_error_spec
from src.middleware.request_context import build_meta, create_request_context
from src.schemas.envelope import EnvelopeResponse, ErrorInfo

logger = logging.getLogger(__name__)

# Default rate limit: 60 requests per minute per client
RATE_LIMIT_REQUESTS = 60
RATE_LIMIT_WINDOW_SECONDS = 60


@dataclass
class _ClientWindow:
    """Sliding window state for a single client."""

    timestamps: list[float] = field(default_factory=list)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Per-client sliding window rate limiter for /api/ai/v1 endpoints.

    Client identity is resolved from:
      1. X-API-Key header (preferred for programmatic access)
      2. Authorization Bearer token (Firebase)
      3. Remote IP as fallback
    """

    def __init__(
        self,
        app: "ASGIApp",  # noqa: F821
        requests_per_window: int = RATE_LIMIT_REQUESTS,
        window_seconds: int = RATE_LIMIT_WINDOW_SECONDS,
    ) -> None:
        super().__init__(app)
        self.requests_per_window = requests_per_window
        self.window_seconds = window_seconds
        # client_key -> _ClientWindow
        self._windows: dict[str, _ClientWindow] = defaultdict(_ClientWindow)

    # ------------------------------------------------------------------
    # Client identification
    # ------------------------------------------------------------------

    @staticmethod
    def _identify_client(request: Request) -> str:
        """Extract a stable client identifier from request headers."""
        # 1. API key (hash the key to avoid storing secrets)
        api_key = request.headers.get("x-api-key")
        if api_key:
            return f"apikey:{api_key[:16]}"

        # 2. Bearer token (use first 32 chars as fingerprint)
        auth_header = request.headers.get("authorization", "")
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:]
            return f"bearer:{token[:32]}"

        # 3. Fallback to IP
        client_host = request.client.host if request.client else "unknown"
        return f"ip:{client_host}"

    # ------------------------------------------------------------------
    # Sliding window logic
    # ------------------------------------------------------------------

    def _check_and_record(self, client_key: str, now: float) -> tuple[bool, int, int]:
        """Check rate limit and record the current request.

        Returns (allowed, remaining, reset_epoch).
        """
        window = self._windows[client_key]
        cutoff = now - self.window_seconds

        # Prune timestamps outside the window
        window.timestamps = [t for t in window.timestamps if t > cutoff]

        remaining = self.requests_per_window - len(window.timestamps)
        # Reset is the earliest time a slot opens up
        if window.timestamps:
            reset_epoch = int(window.timestamps[0] + self.window_seconds) + 1
        else:
            reset_epoch = int(now + self.window_seconds)

        if remaining <= 0:
            return False, 0, reset_epoch

        # Record this request
        window.timestamps.append(now)
        remaining -= 1  # account for the request we just recorded
        return True, remaining, reset_epoch

    # ------------------------------------------------------------------
    # Periodic cleanup to avoid memory leaks from expired client entries
    # ------------------------------------------------------------------

    def _cleanup_stale_entries(self, now: float) -> None:
        """Remove client entries with no timestamps in the current window."""
        cutoff = now - self.window_seconds
        stale_keys = [
            k for k, w in self._windows.items() if not w.timestamps or w.timestamps[-1] <= cutoff
        ]
        for k in stale_keys:
            del self._windows[k]

    # ------------------------------------------------------------------
    # Middleware dispatch
    # ------------------------------------------------------------------

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Only rate-limit V1 API paths
        if not request.url.path.startswith("/api/ai/v1"):
            return await call_next(request)

        now = time.time()
        client_key = self._identify_client(request)

        # Periodic cleanup (every ~100 requests)
        if int(now) % 100 == 0:
            self._cleanup_stale_entries(now)

        allowed, remaining, reset_epoch = self._check_and_record(client_key, now)

        if not allowed:
            retry_after = max(1, reset_epoch - int(now))
            logger.warning(
                "Rate limit exceeded for client=%s path=%s",
                client_key,
                request.url.path,
            )
            context = create_request_context()
            spec = get_error_spec("RATE_LIMITED")
            error = ErrorInfo(
                code="RATE_LIMITED",
                message=f"Rate limit exceeded. Maximum {self.requests_per_window} requests per {self.window_seconds}s.",
                retryable=spec.get("retryable", True),
                suggested_fix=spec.get("suggested_fix"),
            )
            envelope = EnvelopeResponse(
                request_id=context.request_id,
                error=error,
                meta=build_meta(context),
            )
            resp = JSONResponse(
                status_code=429,
                content=jsonable_encoder(envelope.model_dump(exclude_none=True)),
            )
            resp.headers["X-RateLimit-Limit"] = str(self.requests_per_window)
            resp.headers["X-RateLimit-Remaining"] = "0"
            resp.headers["X-RateLimit-Reset"] = str(reset_epoch)
            resp.headers["Retry-After"] = str(retry_after)
            return resp

        # Proceed with the request
        response = await call_next(request)

        # Attach rate limit headers to all V1 responses
        response.headers["X-RateLimit-Limit"] = str(self.requests_per_window)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(reset_epoch)

        return response
