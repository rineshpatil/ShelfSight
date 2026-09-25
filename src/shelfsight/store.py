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
    """Append-only Parquet lake: {root}/{table}/workspace={w}/run_date={d}/{uuid}.parquet, queried with DuckDB.

    The root is a local directory or an s3:// prefix. On S3 the object key is the same layout, and DuckDB
    reads and writes it through httpfs using the instance role (no keys in config).
    """

    def __init__(self, root: str | Path):
        self.root = str(root).rstrip("/") if str(root).startswith("s3://") else Path(root)
        self.is_remote = isinstance(self.root, str)

    def partition(self, table: str, workspace: str, day) -> str:
        part = f"{table}/workspace={workspace}/run_date={day}"
        return f"{self.root}/{part}" if self.is_remote else (self.root / part).as_posix()

    def glob(self, table: str) -> str:
        return f"{self.root}/{table}/**/*.parquet" if self.is_remote else f"{(self.root / table).as_posix()}/**/*.parquet"

    def _connect(self) -> duckdb.DuckDBPyConnection:
        con = duckdb.connect()
        if self.is_remote:
            # credential_chain = instance role on EC2, aws login locally; nothing is stored in the repo
            con.execute("INSTALL httpfs; LOAD httpfs; INSTALL aws; LOAD aws;")
            con.execute("CREATE OR REPLACE SECRET lake (TYPE s3, PROVIDER credential_chain);")
        return con

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
        con = self._connect()
        try:
            con.execute(f"CREATE TABLE w ({TABLES[table]})")
            insert = f"INSERT INTO w VALUES ({', '.join('?' * len(names))})"
            for (workspace, day), values in groups.items():
                out = self.partition(table, workspace, day)
                if not self.is_remote:
                    Path(out).mkdir(parents=True, exist_ok=True)
                con.execute("DELETE FROM w")
                con.executemany(insert, values)
                con.execute(f"COPY w TO '{out}/{uuid.uuid4().hex}.parquet' (FORMAT parquet)")
        finally:
            con.close()
        return len(rows)

    def connect(self) -> duckdb.DuckDBPyConnection:
        """In-memory DuckDB with one view per table. A table with no files yet is empty but still queryable."""
        con = self._connect()
        for table in TABLES:
            typed_nulls = ", ".join(f"NULL::{t} AS {n}" for n, t in columns(table))
            empty = f"CREATE VIEW {table} AS SELECT * FROM (SELECT {typed_nulls}) WHERE false"
            if self.is_remote:
                try:  # cheapest way to ask S3 whether the prefix has any files yet
                    con.execute(f"CREATE VIEW {table} AS SELECT * FROM read_parquet('{self.glob(table)}', "
                                "union_by_name = true, hive_partitioning = false)")
                    con.execute(f"SELECT 1 FROM {table} LIMIT 1")
                except duckdb.Error:
                    con.execute(f"DROP VIEW IF EXISTS {table}")
                    con.execute(empty)
                continue
            base = self.root / table
            if base.exists() and any(base.rglob("*.parquet")):
                con.execute(f"CREATE VIEW {table} AS SELECT * FROM read_parquet('{self.glob(table)}', "
                            "union_by_name = true, hive_partitioning = false)")
            else:
                con.execute(empty)
        return con

    def rows(self, sql: str, params: list | dict | None = None) -> list[dict]:
        con = self.connect()
        try:
            cur = con.execute(sql, params) if params is not None else con.execute(sql)
            names = [d[0] for d in cur.description]
            return [dict(zip(names, row)) for row in cur.fetchall()]
        finally:
            con.close()
