"""DuckDB column definitions for every lake table.

Types must be unparameterised (no DECIMAL(p, s)): columns() splits on commas.
Adding a column is safe because reads use union_by_name; renaming or retyping one is not.
"""

TABLES: dict[str, str] = {
    "runs": """
        run_id VARCHAR, workspace VARCHAR, run_date DATE, status VARCHAR,
        started_at TIMESTAMP, finished_at TIMESTAMP,
        planned_calls INTEGER, ok_calls INTEGER, error_calls INTEGER, skipped_calls INTEGER, probe_errors INTEGER,
        coverage DOUBLE, quota_exhausted VARCHAR[], input_tokens BIGINT, output_tokens BIGINT
    """,
    "raw_responses": """
        response_id VARCHAR, run_id VARCHAR, workspace VARCHAR, run_date DATE,
        prompt_id VARCHAR, prompt_version INTEGER, intent VARCHAR, priority VARCHAR,
        engine VARCHAR, model VARCHAR, mode VARCHAR, sample_n INTEGER,
        status VARCHAR, error VARCHAR,
        response_text VARCHAR, web_search_queries VARCHAR[], raw_json VARCHAR,
        system_prompt VARCHAR, temperature DOUBLE,
        input_tokens INTEGER, output_tokens INTEGER, latency_ms INTEGER, created_at TIMESTAMP
    """,
    "citations": """
        response_id VARCHAR, workspace VARCHAR, run_date DATE, engine VARCHAR, mode VARCHAR, prompt_id VARCHAR,
        position INTEGER, url VARCHAR, domain VARCHAR, domain_type VARCHAR, brand_id VARCHAR
    """,
    "candidates": """
        response_id VARCHAR, workspace VARCHAR, run_date DATE, engine VARCHAR, prompt_id VARCHAR,
        search_query VARCHAR, query_index INTEGER, position INTEGER,
        url VARCHAR, domain VARCHAR, domain_type VARCHAR, source_engine VARCHAR, brand_id VARCHAR,
        probed_at TIMESTAMP
    """,
    # source: llm | dictionary | both | none. A "none" row is a sentinel meaning
    # "extracted, no brands found", so a response is never re-extracted or mistaken for unextracted.
    "mentions": """
        response_id VARCHAR, workspace VARCHAR, run_date DATE, engine VARCHAR, mode VARCHAR, prompt_id VARCHAR,
        brand_id VARCHAR, brand_raw VARCHAR, rank INTEGER, is_recommended BOOLEAN,
        sentiment VARCHAR, claims VARCHAR[], source VARCHAR, extractor_version VARCHAR
    """,
    "funnel": """
        workspace VARCHAR, run_date DATE, engine VARCHAR, mode VARCHAR, prompt_id VARCHAR, prompt_version INTEGER,
        intent VARCHAR, priority VARCHAR, brand_id VARCHAR, is_client BOOLEAN, response_id VARCHAR,
        is_eligible BOOLEAN, is_cited BOOLEAN, is_named BOOLEAN, is_recommended BOOLEAN, rank INTEGER,
        eligible_n INTEGER, diagnosis VARCHAR, extractor_version VARCHAR, joiner_version VARCHAR
    """,
}


def columns(table: str) -> list[tuple[str, str]]:
    """[(name, type), ...] in DDL order."""
    parts = (c.strip() for c in TABLES[table].split(","))
    return [tuple(c.split(maxsplit=1)) for c in parts if c]
