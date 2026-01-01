FROM prefecthq/prefect:3.6.9-python3.14

# Install uv package manager
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Set working directory
WORKDIR /app

# Copy dependency files and install dependencies (cached layer)
# Remove any existing .venv to avoid Windows/Linux conflicts
COPY pyproject.toml uv.lock ./
RUN rm -rf .venv && uv sync --frozen --no-dev --no-install-project

# Copy application code
COPY flows/ ./flows/
COPY tasks/ ./tasks/
COPY blocks/ ./blocks/
COPY *.py ./

# Final sync to complete installation
RUN uv sync --frozen --no-dev

# Expose Prefect server port
EXPOSE 4200

# Set environment variables - add .venv/bin to PATH
ENV PYTHONPATH=/app \
    PATH="/app/.venv/bin:$PATH"

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:${PREFECT_SERVER_API_PORT:-4200}/api/health')" || exit 1

# Override the Prefect base image entrypoint and use uv run
ENTRYPOINT []
CMD ["uv", "run", "prefect", "server", "start", "--host", "0.0.0.0"]