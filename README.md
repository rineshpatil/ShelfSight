# ShelfSight

AEO diagnosis for agencies: why AI assistants do or don't recommend a client brand, measured
through the retrieval chain the assistant actually ran. Design:
`docs/superpowers/specs/2026-09-21-shelfsight-design.md`.

## Run locally

    uv sync
    docker compose -f infra/searxng/docker-compose.yml up -d
    export GEMINI_API_KEY=...      # Google AI Studio, free tier
    export GROQ_API_KEY=...        # console.groq.com, free tier
    uv run python scripts/smoke_gemini.py
    uv run shelfsight collect --workspace dotandkey --limit 3
    uv run shelfsight extract --workspace dotandkey
    uv run shelfsight funnel  --workspace dotandkey
    uv run shelfsight report  --workspace dotandkey

Data lands in `data/lake/` (git-ignored). `uv run pytest` runs the offline test suite.
Live checks are in `scripts/` and are never run in CI.
