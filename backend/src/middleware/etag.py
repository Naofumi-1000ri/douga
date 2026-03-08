"""ETag conditional request middleware for V1 API.

Handles If-None-Match checks for GET endpoints that set ETag headers.
When a client sends If-None-Match with a matching ETag value, returns
304 Not Modified with an empty body instead of re-sending the full response.

Also adds Cache-Control headers to semi-static endpoints like
/capabilities and /schemas.
"""

import logging

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

logger = logging.getLogger(__name__)

# Paths that are semi-static and benefit from client-side caching
_CACHEABLE_PATHS = {"/api/ai/v1/capabilities", "/api/ai/v1/schemas"}
_CACHE_CONTROL_STATIC = "public, max-age=300"


class ETagMiddleware(BaseHTTPMiddleware):
    """Middleware that handles ETag-based conditional requests for V1 API.

    For GET requests to /api/ai/v1/* with an If-None-Match header:
    - If the response ETag matches If-None-Match, returns 304 (empty body).
    - Otherwise, passes through the full response.

    Also adds Cache-Control to semi-static endpoints (/capabilities, /schemas).
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Only handle V1 API GET requests
        if not request.url.path.startswith("/api/ai/v1") or request.method != "GET":
            return await call_next(request)

        if_none_match = request.headers.get("if-none-match")

        response = await call_next(request)

        # Add Cache-Control for semi-static endpoints
        if request.url.path in _CACHEABLE_PATHS:
            response.headers.setdefault("Cache-Control", _CACHE_CONTROL_STATIC)

        # Check ETag match for conditional requests
        if if_none_match and response.status_code == 200:
            response_etag = response.headers.get("etag")
            if response_etag and self._etag_matches(if_none_match, response_etag):
                logger.debug("ETag match for %s, returning 304", request.url.path)
                return Response(
                    status_code=304,
                    headers={
                        "ETag": response_etag,
                        # Preserve rate limit headers if present
                        **{
                            k: v
                            for k, v in response.headers.items()
                            if k.lower().startswith("x-ratelimit")
                        },
                    },
                )

        return response

    @staticmethod
    def _etag_matches(if_none_match: str, etag: str) -> bool:
        """Check if the If-None-Match header matches the response ETag.

        Supports:
        - Exact match: If-None-Match: W/"abc"
        - Wildcard: If-None-Match: *
        - Multiple values: If-None-Match: W/"abc", W/"def"
        """
        if if_none_match.strip() == "*":
            return True

        # Parse comma-separated ETags
        client_etags = [e.strip() for e in if_none_match.split(",")]
        return etag in client_etags
