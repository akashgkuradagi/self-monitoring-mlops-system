# Multi-stage production Dockerfile for Self-Monitoring MLOps System (PKG-01)
# Stage 1: Build dependency wheels
FROM python:3.11-slim AS builder

WORKDIR /build

# Install build dependencies required for compiling native extensions
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment for clean layer separation
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt


# Stage 2: Minimal runtime image
FROM python:3.11-slim AS runner

LABEL maintainer="Self-Monitoring MLOps Team"
LABEL description="Production container for Self-Monitoring MLOps Service (PKG-01)"
LABEL version="0.1.0"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    PORT=8000 \
    ENVIRONMENT=production

# Install curl for container health check probe
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy pre-built virtual environment from builder stage
COPY --from=builder /opt/venv /opt/venv

# Create unprivileged application user and persistent storage directories
RUN groupadd -g 1001 mlops && \
    useradd -u 1001 -g mlops -m -s /bin/bash mlops && \
    mkdir -p /data /app/models /app/mlruns && \
    chown -R mlops:mlops /data /app

WORKDIR /app

# Copy application modules and necessary runtime artifacts
COPY --chown=mlops:mlops app/ ./app/
COPY --chown=mlops:mlops data/ ./data/
COPY --chown=mlops:mlops scripts/ ./scripts/
COPY --chown=mlops:mlops pyproject.toml ./

# Switch to non-root user
USER mlops

EXPOSE 8000

HEALTHCHECK --interval=20s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

ENTRYPOINT ["uvicorn", "app.main:app"]
CMD ["--host", "0.0.0.0", "--port", "8000"]
