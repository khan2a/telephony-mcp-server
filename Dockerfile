# Use Python 3.13 as base image with uv pre-installed
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

# Set working directory
WORKDIR /app

# Copy project files
COPY pyproject.toml uv.lock ./
COPY servers/ ./servers/
COPY utils/ ./utils/
COPY .env private.key ./
COPY main.py ./

# Install dependencies using uv
RUN uv pip install --system -e .

# Expose ports for both MCP server and callback server
EXPOSE 8000 8080

# Set environment variables
ENV UV_SYSTEM_PYTHON=1
ENV PYTHONUNBUFFERED=1

# Run the telephony server
CMD ["uv", "run", "main.py"]
