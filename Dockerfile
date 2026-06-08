FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js (for GCP gcloud-mcp)
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Install uv (for AWS MCP servers)
RUN curl -LsSf https://astral.sh/uv/install.sh | sh \
    && ln -s /root/.local/bin/uv /usr/local/bin/uv \
    && ln -s /root/.local/bin/uvx /usr/local/bin/uvx

# Pre-download the embedding model
RUN uv pip install --system --no-cache sentence-transformers && \
    python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

# Install cloud-engineer-mcp (runtime + optional OpenTelemetry tracing only;
# dev tooling like ruff/mypy/pytest is intentionally excluded from the image).
# README.md and LICENSE are required by the build backend: pyproject.toml
# declares `readme = "README.md"` and `license-files = ["LICENSE"]`, so they
# must be present when hatchling builds the wheel.
COPY pyproject.toml README.md LICENSE ./
COPY src/ src/
RUN uv pip install --system --no-cache ".[otel]"

# Default config
COPY config.example.yml config.yml

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

ENTRYPOINT ["python", "-m", "cloud_engineer_mcp", "serve", "--transport", "http", "--host", "0.0.0.0"]
