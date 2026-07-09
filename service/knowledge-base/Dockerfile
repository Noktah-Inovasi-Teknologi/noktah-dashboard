FROM python:3.14-slim

# Install uv package manager
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Set working directory
WORKDIR /app

# Copy dependency files and install dependencies (cached layer)
COPY pyproject.toml uv.lock ./
RUN rm -rf .venv && uv sync --frozen --no-dev --no-install-project

# Copy application code
COPY *.py ./
COPY agent/ ./agent/

# Final sync to complete installation
RUN uv sync --frozen --no-dev

# MCP server listens over SSE HTTP transport (research.md R1)
EXPOSE 8080

ENV PYTHONPATH=/app \
    PATH="/app/.venv/bin:$PATH"

# Health check hits the SSE server's base path
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')" || exit 1

CMD ["uv", "run", "python", "main.py"]
