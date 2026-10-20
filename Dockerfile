FROM ghcr.io/astral-sh/uv:0.13.1 AS uv

FROM python:3.12-slim-bookworm AS builder

COPY --from=uv /uv /usr/local/bin/uv
ENV UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE NOTICE ./
COPY breviabook ./breviabook
RUN uv sync --locked --no-dev --extra pdf --no-editable
ENV TIKTOKEN_CACHE_DIR=/opt/tiktoken-cache
RUN .venv/bin/python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')"

FROM python:3.12-slim-bookworm AS runtime

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libharfbuzz-subset0 \
        libgdk-pixbuf-2.0-0 \
        fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 breviabook \
    && useradd --uid 10001 --gid 10001 --no-create-home \
        --home-dir /tmp --shell /usr/sbin/nologin breviabook \
    && mkdir /input /output \
    && chown 10001:10001 /output

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /opt/tiktoken-cache /opt/tiktoken-cache
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp \
    XDG_CACHE_HOME=/tmp/.cache \
    TIKTOKEN_CACHE_DIR=/opt/tiktoken-cache \
    LITELLM_LOCAL_MODEL_COST_MAP=True \
    OLLAMA_ENDPOINT=http://host.docker.internal:11434

USER 10001:10001
WORKDIR /output
ENTRYPOINT ["breviabook"]
CMD ["--help"]
