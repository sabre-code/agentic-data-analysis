FROM python:3.12-slim-bullseye

WORKDIR /app

RUN apt-get -y update && \
    apt-get -y upgrade && \
    apt-get -y clean && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --upgrade pip setuptools wheel
RUN pip install --no-cache-dir uv

# Create venv and install deps via uv
RUN python -m venv /app/venv
ENV PATH="/app/venv/bin:$PATH"

RUN uv pip install flask pandas numpy pyarrow

# Non-root group + user
RUN groupadd -g 1001 sandbox && \
    useradd -r -u 1001 -g sandbox sandboxuser && \
    chown -R sandboxuser:sandbox /app

COPY --chown=sandboxuser:sandbox server.py .

USER 1001

EXPOSE 8080

CMD ["python", "server.py"]
