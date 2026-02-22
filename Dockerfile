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

# Install system dependencies for PDF/image generation and Chrome for Kaleido
# libfreetype6-dev, libjpeg-dev, zlib1g-dev: Required for Pillow and reportlab
# Chrome dependencies: Required for Kaleido to convert Plotly charts to PNG
RUN apt-get -y update && \
    apt-get -y upgrade && \
    apt-get install -y --no-install-recommends \
        libfreetype6-dev \
        libjpeg-dev \
        zlib1g-dev \
        libpng-dev \
        # Chrome dependencies for Kaleido
        wget \
        gnupg \
        ca-certificates \
        fonts-liberation \
        libasound2 \
        libatk-bridge2.0-0 \
        libatk1.0-0 \
        libcups2 \
        libdbus-1-3 \
        libdrm2 \
        libgbm1 \
        libgtk-3-0 \
        libnspr4 \
        libnss3 \
        libxcomposite1 \
        libxdamage1 \
        libxfixes3 \
        libxkbcommon0 \
        libxrandr2 \
        xdg-utils && \
    # Install Chrome
    wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | apt-key add - && \
    echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google-chrome.list && \
    apt-get -y update && \
    apt-get install -y --no-install-recommends google-chrome-stable && \
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

# Data directories for shared volumes
RUN mkdir -p /data/uploads /data/reports && chown -R python:python /data

RUN mkdir -p /home/python && chown -R python:python /home/python

USER 999
ENV PATH="/usr/application/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')" || exit 1

CMD ["/bin/sh", "uvicorn.sh"]
