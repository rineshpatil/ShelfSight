import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from shelfsight.schemas import TABLES, columns


def utcnow() -> datetime:
    """Naive UTC timestamp. The lake stores TIMESTAMP, not TIMESTAMPTZ."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Store:
    """Append-only Parquet lake: {root}/{table}/workspace={w}/run_date={d}/{uuid}.parquet, queried with DuckDB."""

    def __init__(self, root: str | Path):
        self.root = Path(root)  # ponytail: local directory only; Plan 3 adds an s3:// root via httpfs

    def write(self, table: str, rows: list[dict]) -> int:
        if not rows:
            return 0
        names = [n for n, _ in columns(table)]
        groups: dict[tuple[str, str], list[tuple]] = defaultdict(list)
        for r in rows:
            unknown = set(r) - set(names)
            if unknown:
                raise ValueError(f"{table}: unknown columns {sorted(unknown)}")
            groups[(r["workspace"], str(r["run_date"]))].append(tuple(r.get(n) for n in names))
        con = duckdb.connect()
        try:
            con.execute(f"CREATE TABLE w ({TABLES[table]})")
            insert = f"INSERT INTO w VALUES ({', '.join('?' * len(names))})"
            for (workspace, day), values in groups.items():
                out = self.root / table / f"workspace={workspace}" / f"run_date={day}"
                out.mkdir(parents=True, exist_ok=True)
                con.execute("DELETE FROM w")
                con.executemany(insert, values)
                con.execute(f"COPY w TO '{(out / f'{uuid.uuid4().hex}.parquet').as_posix()}' (FORMAT parquet)")
        finally:
            con.close()
        return len(rows)

    def connect(self) -> duckdb.DuckDBPyConnection:
        """In-memory DuckDB with one view per table. A table with no files yet is empty but still queryable."""
        con = duckdb.connect()
        for table in TABLES:
            base = self.root / table
            if base.exists() and any(base.rglob("*.parquet")):
                src = f"read_parquet('{base.as_posix()}/**/*.parquet', union_by_name = true, hive_partitioning = false)"
            else:
                typed_nulls = ", ".join(f"NULL::{t} AS {n}" for n, t in columns(table))
                src = f"(SELECT {typed_nulls}) WHERE false"
            con.execute(f"CREATE VIEW {table} AS SELECT * FROM {src}")
        return con

    def rows(self, sql: str, params: list | dict | None = None) -> list[dict]:
        con = self.connect()
        try:
            cur = con.execute(sql, params) if params is not None else con.execute(sql)
            names = [d[0] for d in cur.description]
            return [dict(zip(names, row)) for row in cur.fetchall()]
        finally:
            con.close()
