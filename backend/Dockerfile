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

# ARCH-29 Tranche 1 — pre-bake the embedding weights.
#
# The `ocr` target below has pre-baked PaddleOCR since ARCH-10 and the reranker
# image has pre-baked its CrossEncoder since ARCH-11. This target did not, so
# `embedding_service._load_model()` pulled all-MiniLM-L6-v2 from the Hugging
# Face Hub on the first vector generation of a cold container — over the
# network, unauthenticated, on the request path.
#
# WHY THE NAME COMES FROM `canonical_model_name` AND IS NOT WRITTEN OUT HERE
# =========================================================================
#
# `SentenceTransformer("all-MiniLM-L6-v2")` and
# `SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")` load the same
# weights into DIFFERENT cache directories. `settings.EMBEDDING_MODEL_NAME` is
# the bare form and the `document_settings` default is the qualified form —
# two strings, one model, as `app/core/embeddings.py` has documented since
# ARCH-11. Baking under one spelling and loading under the other produces a
# cache MISS and a silent runtime download: the optimisation appears to be
# applied, the image grows by 90MB, and nothing gets faster.
#
# Resolving through `canonical_model_name` at build time means the string baked
# is by construction the string loaded. `verify_arch29_tranche1.py` G7 asserts
# both configured spellings still canonicalise to the same known model, so a
# future config edit that breaks the equality fails the gate rather than
# quietly reintroducing the download.
RUN python -c "\
from app.core.embeddings import canonical_model_name; \
from app.core.config import settings; \
from sentence_transformers import SentenceTransformer; \
name = canonical_model_name(settings.EMBEDDING_MODEL_NAME); \
print('ARCH-29 pre-baking embedding model:', name); \
SentenceTransformer(name)" || \
    (echo 'Embedding warmup notice: weights will download on first run' && true)

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