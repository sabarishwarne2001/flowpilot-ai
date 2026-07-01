# Stage 1: Build virtual environment and compile dependencies
FROM python:3.12-slim AS builder

WORKDIR /build

# Configure build execution settings
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Install system dependencies needed to compile python packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Establish a structured virtual environment pathway
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .

# Install dependencies into the virtual environment layer
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt


# Stage 2: Establish the final secure runtime environment
FROM python:3.12-slim AS runner

WORKDIR /app

# Configure runtime execution behaviors
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Copy the compiled virtual environment from builder stage
COPY --from=builder /opt/venv /opt/venv

# Copy only the required application source assets
COPY app/ ./app
COPY .env.example .

# Add a non-root system user and group to run the app securely
RUN addgroup --system appgroup && adduser --system --group appuser \
    && chown -r appuser:appgroup /workspace

# Run container logic as a non-privileged system user
USER appuser

EXPOSE 8000

# Start server process mapping to the standard virtual environment's execution commands
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]