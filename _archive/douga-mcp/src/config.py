"""Configuration for douga MCP server."""

import os

# Backend API URL (default: local development)
API_BASE_URL = os.environ.get(
    "DOUGA_API_URL",
    "http://localhost:8000",
)

# API key for authentication (optional, uses dev-token fallback)
API_KEY = os.environ.get("DOUGA_API_KEY", "")

# Dev mode auth token
DEV_TOKEN = "dev-token"

# Request timeout in seconds
REQUEST_TIMEOUT = 120.0

# Upload timeout (longer for large files)
UPLOAD_TIMEOUT = 300.0
