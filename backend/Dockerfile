# =============================================================================
# FlowPilot AI — Multi-Stage Production Dockerfile (ARCH-10 Step 8)
#
# Build targets:
#   docker build --target web    -t flowpilot/web:latest    .
#   docker build --target worker -t flowpilot/worker:latest .  # light
#   docker build --target enrich -t flowpilot/enrich:latest .
#   docker build --target ocr    -t flowpilot/ocr:latest    .
# =============================================================================

# -----------------------------------------------------------------------------
# Base Stage: Shared Python 3.12 runtime, dependencies & non-root user
# -----------------------------------------------------------------------------
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/home/flowpilot

WORKDIR /app

# Core runtime packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install base Python dependencies
COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application codebase
COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
COPY scripts ./scripts
COPY .env.example .

# Create non-root application user
RUN addgroup --system flowpilot && \
    adduser --system --ingroup flowpilot --home /home/flowpilot flowpilot && \
    mkdir -p /home/flowpilot/.cache && \
    chown -R flowpilot:flowpilot /app /home/flowpilot

USER flowpilot


# -----------------------------------------------------------------------------
# Target: web (FastAPI API Web Tier — Zero ML dependencies)
# -----------------------------------------------------------------------------
FROM base AS web

ENV STORAGE_BACKEND=r2

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/api/v1/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]


# -----------------------------------------------------------------------------
# Target: worker (Relay, Delivery & Light Jobs Profile)
# -----------------------------------------------------------------------------
FROM base AS worker

ENV WORKER_PROFILE=light

CMD ["python", "-m", "app.worker", "--loop", "jobs", "--profile", "light"]


# -----------------------------------------------------------------------------
# Target: enrich (Embeddings, ChromaDB, BM25 & Summarization Worker)
# -----------------------------------------------------------------------------
FROM base AS enrich

USER root
# Ensure writable HuggingFace cache for non-root user
RUN mkdir -p /home/flowpilot/.cache/huggingface && \
    chown -R flowpilot:flowpilot /home/flowpilot/.cache/huggingface
USER flowpilot

ENV WORKER_PROFILE=enrich \
    TRANSFORMERS_OFFLINE=0 \
    HF_HOME=/home/flowpilot/.cache/huggingface

CMD ["python", "-m", "app.worker", "--loop", "jobs", "--profile", "enrich"]


# -----------------------------------------------------------------------------
# Target: ocr (Self-hosted PaddleOCR Extraction Worker)
# -----------------------------------------------------------------------------
FROM base AS ocr

USER root
# Native graphics and multiprocessing libraries required by OpenCV / PaddleOCR
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    libsm6 \
    libxext6 \
    libxrender1 \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /home/flowpilot/.cache/paddle && \
    mkdir -p /home/flowpilot/.paddlex && \
    chown -R flowpilot:flowpilot /home/flowpilot/.cache /home/flowpilot/.paddlex
USER flowpilot

ENV WORKER_PROFILE=ocr \
    OMP_NUM_THREADS=1 \
    PADDLE_HOME=/home/flowpilot/.cache/paddle \
    PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True \
    FLAGS_use_mkldnn=0 \
    FLAGS_enable_pir_api=0 \
    FLAGS_enable_pir_in_executor=0 \
    KMP_DUPLICATE_LIB_OK=TRUE

# Pre-bake model detection and recognition weights at image build time
RUN python -c "from app.services.ocr.paddle import PaddleOCRProvider; PaddleOCRProvider()._build_engine()" || \
    (echo 'PaddleOCR warmup notice: Model weights will download on first run' && true)

CMD ["python", "-m", "app.worker", "--loop", "jobs", "--profile", "ocr"]