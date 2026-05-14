FROM python:3.12-slim

WORKDIR /app

# System deps — none needed (browser runs in the separate VNC container)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps — copy src/ first so setuptools can find the package
ARG DEV=false
COPY pyproject.toml .
COPY src/ ./src/
RUN if [ "$DEV" = "true" ]; then \
      pip install --no-cache-dir -e ".[dev]"; \
    else \
      pip install --no-cache-dir .; \
    fi

# Seed the action script so the volume is pre-populated on first deploy.
# Docker copies image content into an empty named volume on first mount.
# On subsequent restarts the volume retains whatever the service recorded.
COPY data/action_script.json /data/action_script.json

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -f http://localhost:8001/health || exit 1

CMD ["uvicorn", "cookie_refresher.infrastructure.main:app", "--host", "0.0.0.0", "--port", "8001"]
