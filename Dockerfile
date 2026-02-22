# ─────────────────────────────────────────────────────────────
# Stage 1 — build: create venv and install all dependencies
# ─────────────────────────────────────────────────────────────
FROM python:3.12-slim-bullseye AS build

WORKDIR /usr/application

# Create isolated venv
RUN python -m venv /usr/application/venv
ENV PATH="/usr/application/venv/bin:$PATH"

COPY uv.lock .
COPY pyproject.toml .

RUN pip install --no-cache-dir --upgrade pip setuptools wheel
RUN pip install --no-cache-dir uv
RUN uv pip install -r pyproject.toml


# ─────────────────────────────────────────────────────────────
# Stage 2 — runtime: minimal image with app + venv only
# ─────────────────────────────────────────────────────────────
FROM python:3.12-slim-bullseye AS runtime

# System updates and cleanup
RUN apt-get -y update && \
    apt-get -y upgrade && \
    apt-get -y clean && \
    rm -rf /var/lib/apt/lists/*

# Non-root group + user
RUN groupadd -g 999 python && \
    useradd -r -u 999 -g python python

RUN pip install --no-cache-dir --upgrade pip setuptools wheel

RUN mkdir /usr/application && chown python:python /usr/application
WORKDIR /usr/application

# Copy pre-built venv and application source from build stage
COPY --chown=python:python --from=build /usr/application/venv ./venv
COPY --chown=python:python app/ ./app/
COPY --chown=python:python uvicorn.sh ./uvicorn.sh

# Data directory for shared volume
RUN mkdir -p /data/uploads && chown -R python:python /data

RUN mkdir -p /home/python && chown -R python:python /home/python

USER 999
ENV PATH="/usr/application/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')" || exit 1

CMD ["/bin/sh", "uvicorn.sh"]
