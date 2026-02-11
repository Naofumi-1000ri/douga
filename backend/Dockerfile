FROM python:3.11-slim

# Git hash for version tracking
ARG GIT_HASH=unknown
ENV GIT_HASH=${GIT_HASH}

# Install FFmpeg and other dependencies
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libpq-dev \
    gcc \
    git \
    fonts-noto-cjk \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Install uv
RUN pip install uv

WORKDIR /app

# Copy all source files first (needed for install)
COPY pyproject.toml ./
COPY src/ ./src/
COPY migrations/ ./migrations/

# Install dependencies (not editable mode for production)
RUN uv pip install --system .

# Create non-root user
RUN useradd -m appuser && chown -R appuser:appuser /app
USER appuser

# Expose port
EXPOSE 8000

# Run the application
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
