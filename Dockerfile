# ===========================================================================
# RiskSentinel — Dockerfile  (multi-stage)
# ===========================================================================

# ── Stage 1: dependency install ────────────────────────────────────────────
FROM python:3.12-slim AS deps

WORKDIR /app

# Install system deps needed to compile asyncpg / numpy
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        python3-dev \
        libffi-dev \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt


# ── Stage 2: production image ──────────────────────────────────────────────
FROM python:3.12-slim AS production

WORKDIR /app

# Only the shared-lib needed at runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq-client \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from stage 1
COPY --from=deps /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=deps /usr/local/bin/uvicorn               /usr/local/bin/uvicorn

# Copy application code
COPY . .

# Non-root user for security
RUN groupadd -r appuser && useradd -r -g appuser appuser
USER appuser

# Expose
EXPOSE 8000

# Health-check (curl not available in slim; use python)
HEALTHCHECK --interval=15s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/v1/health')" || exit 1

# Default entrypoint — production-grade uvicorn
ENTRYPOINT ["uvicorn", "app.main:app"]
CMD ["--host", "0.0.0.0", "--port", "8000", "--workers", "4", "--log-level", "info"]
