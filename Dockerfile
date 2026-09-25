# ShelfSight pipeline + bridge, built for the ARM64 EC2 host by CDK and pushed to ECR.
FROM python:3.12-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /usr/local/bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/app/.venv PATH=/app/.venv/bin:$PATH

# Dependencies first so code edits don't re-resolve them
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
COPY config ./config
COPY data/prompts_seed.csv ./data/prompts_seed.csv
RUN uv sync --frozen --no-dev

# Fetch the DuckDB extensions at build time: the instance has no need to download them on first run
RUN python -c "import duckdb; duckdb.execute('INSTALL httpfs; INSTALL aws;')" \
 && useradd --create-home --uid 10001 shelfsight \
 && cp -r /root/.duckdb /home/shelfsight/.duckdb \
 && chown -R shelfsight:shelfsight /home/shelfsight /app
USER shelfsight

# SHELFSIGHT_LAKE is the s3:// prefix; the token and API keys come from SSM via the compose env file
ENV SHELFSIGHT_LAKE=s3://changeme/lake
EXPOSE 8765
CMD ["sh", "-c", "exec shelfsight --lake \"$SHELFSIGHT_LAKE\" serve --host 0.0.0.0 --port 8765"]
