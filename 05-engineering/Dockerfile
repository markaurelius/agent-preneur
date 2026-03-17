FROM python:3.11-slim

# Build deps needed for chromadb (hnswlib C++ compilation)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Install dependencies first (cached layer — no project code needed yet)
COPY pyproject.toml .
RUN uv sync --no-install-project --all-extras

# Copy application code
COPY . .

# Create data directories (will be overridden by bind mounts in compose)
RUN mkdir -p /app/data /app/chroma

ENV PATH="/app/.venv/bin:$PATH"

CMD ["bash"]
