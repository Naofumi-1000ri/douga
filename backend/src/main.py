import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.api import ai, ai_v1, ai_video, assets, auth, folders, members, operations, preview, projects, render, sequences, storage, transcription
from src.config import get_settings
from src.constants.error_codes import get_error_spec
from src.middleware.request_context import build_meta, create_request_context
from src.models.database import engine, init_db, sync_engine
from src.schemas.envelope import EnvelopeResponse, ErrorInfo

settings = get_settings()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # Startup
    await init_db()
    yield
    # Shutdown
    await engine.dispose()
    sync_engine.dispose()


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _is_ai_v1_path(request: Request) -> bool:
    return request.url.path.startswith("/api/ai/v1")


def _http_error_code(status_code: int) -> str:
    mapping = {
        400: "BAD_REQUEST",
        401: "UNAUTHORIZED",
        403: "FORBIDDEN",
        404: "NOT_FOUND",
        409: "CONCURRENT_MODIFICATION",
        422: "VALIDATION_ERROR",
        429: "RATE_LIMITED",
        500: "INTERNAL_ERROR",
    }
    return mapping.get(status_code, "HTTP_ERROR")


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Handle FastAPI request validation errors (422) with envelope format for v1 API."""
    if _is_ai_v1_path(request):
        context = create_request_context()
        spec = get_error_spec("VALIDATION_ERROR")

        # Build a human-readable message from validation errors
        errors = exc.errors()
        if errors:
            first_error = errors[0]
            loc = " -> ".join(str(x) for x in first_error.get("loc", []))
            msg = first_error.get("msg", "Validation error")
            message = f"{loc}: {msg}" if loc else msg
        else:
            message = "Request validation failed"

        error = ErrorInfo(
            code="VALIDATION_ERROR",
            message=message,
            retryable=spec.get("retryable", False),
            suggested_fix=spec.get("suggested_fix"),
        )
        envelope = EnvelopeResponse(
            request_id=context.request_id,
            error=error,
            meta=build_meta(context),
        )
        return JSONResponse(
            status_code=422,
            content=jsonable_encoder(envelope.model_dump(exclude_none=True)),
        )

    # Default FastAPI format for non-v1 paths
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors()},
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    if _is_ai_v1_path(request):
        context = create_request_context()
        error_code = _http_error_code(exc.status_code)
        spec = get_error_spec(error_code)
        error = ErrorInfo(
            code=error_code,
            message=str(exc.detail),
            retryable=spec.get("retryable", False),
            suggested_fix=spec.get("suggested_fix"),
        )
        envelope = EnvelopeResponse(
            request_id=context.request_id,
            error=error,
            meta=build_meta(context),
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=jsonable_encoder(envelope.model_dump(exclude_none=True)),
        )

    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
    )


# Global exception handler to ensure errors return proper JSON
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception(f"Unhandled exception: {exc}")
    if _is_ai_v1_path(request):
        context = create_request_context()
        spec = get_error_spec("INTERNAL_ERROR")
        error = ErrorInfo(
            code="INTERNAL_ERROR",
            message="Internal server error",
            retryable=spec.get("retryable", False),
            suggested_fix=spec.get("suggested_fix"),
        )
        envelope = EnvelopeResponse(
            request_id=context.request_id,
            error=error,
            meta=build_meta(context),
        )
        return JSONResponse(
            status_code=500,
            content=jsonable_encoder(envelope.model_dump(exclude_none=True)),
        )

    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


# Routers
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(projects.router, prefix="/api/projects", tags=["projects"])
app.include_router(assets.router, prefix="/api", tags=["assets"])
app.include_router(folders.router, prefix="/api", tags=["folders"])
app.include_router(render.router, prefix="/api", tags=["render"])
app.include_router(transcription.router, prefix="/api", tags=["transcription"])
app.include_router(storage.router, prefix="/api/storage", tags=["storage"])
app.include_router(ai.router, prefix="/api/ai", tags=["ai"])
app.include_router(ai_v1.router, prefix="/api/ai/v1", tags=["ai-v1"])
app.include_router(ai_video.router, prefix="/api/ai-video", tags=["ai-video"])
app.include_router(preview.router, prefix="/api", tags=["preview"])
app.include_router(members.router, prefix="/api", tags=["members"])
app.include_router(operations.router, prefix="/api/projects", tags=["operations"])
app.include_router(sequences.router, prefix="/api/projects", tags=["sequences"])


@app.get("/health")
async def health_check() -> dict[str, str]:
    return {"status": "healthy", "version": settings.app_version, "git_hash": settings.git_hash}


@app.get("/api/version")
async def get_version() -> dict[str, str]:
    """Return the backend version info."""
    return {"version": settings.app_version, "git_hash": settings.git_hash}
