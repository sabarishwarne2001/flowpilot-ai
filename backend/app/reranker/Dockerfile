# =============================================================================
# FlowPilot AI — Reranker Microservice Image (ARCH-11 Step 7)
# =============================================================================

FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_HOME=/models \
    SENTENCE_TRANSFORMERS_HOME=/models

WORKDIR /srv

# CPU-only torch to maintain lightweight footprint
RUN pip install --no-cache-dir \
        "torch==2.6.0" --index-url https://download.pytorch.org/whl/cpu \
 && pip install --no-cache-dir \
        "sentence-transformers==5.6.0" \
        "fastapi==0.115.6" \
        "uvicorn[standard]==0.34.0" \
        "pydantic==2.10.4"

ARG RERANKER_MODEL=cross-encoder/ms-marco-MiniLM-L-6-v2
ENV RERANKER_MODEL=${RERANKER_MODEL}

# Pre-download and bake model weights into the container image
RUN python -c "from sentence_transformers import CrossEncoder; CrossEncoder('${RERANKER_MODEL}', max_length=512)"

COPY app/reranker /srv/app/reranker

RUN useradd --create-home --uid 10001 reranker \
 && chown -R reranker:reranker /srv /models
USER reranker

EXPOSE 8081

HEALTHCHECK --interval=15s --timeout=3s --start-period=60s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8081/ready', timeout=2).status==200 else 1)"

CMD ["uvicorn", "app.reranker.main:app", "--host", "0.0.0.0", "--port", "8081", "--workers", "1"]