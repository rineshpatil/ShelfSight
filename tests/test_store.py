from datetime import date

import pytest

from shelfsight.store import Store


def row(**kw):
    base = dict(response_id="r1", workspace="ws", run_date=date(2026, 9, 21), engine="gemini", mode="web",
                prompt_id="P1", position=1, url="https://a.com/", domain="a.com", domain_type="other", brand_id=None)
    return {**base, **kw}


def test_write_then_read_roundtrip(tmp_path):
    s = Store(tmp_path)
    assert s.write("citations", [row(), row(position=2, url="https://b.com/", domain="b.com")]) == 2
    got = s.rows("SELECT position, domain FROM citations ORDER BY position")
    assert got == [{"position": 1, "domain": "a.com"}, {"position": 2, "domain": "b.com"}]


def test_writes_are_partitioned_by_workspace_and_date(tmp_path):
    s = Store(tmp_path)
    s.write("citations", [row(), row(response_id="r2", run_date=date(2026, 9, 22))])
    dirs = sorted(p.relative_to(tmp_path).parent.as_posix() for p in tmp_path.rglob("*.parquet"))
    assert dirs == ["citations/workspace=ws/run_date=2026-09-21", "citations/workspace=ws/run_date=2026-09-22"]


def test_second_write_to_same_partition_appends_a_file(tmp_path):
    s = Store(tmp_path)
    s.write("citations", [row()])
    s.write("citations", [row(response_id="r2")])
    assert len(list(tmp_path.rglob("*.parquet"))) == 2
    assert s.rows("SELECT count(*) AS n FROM citations") == [{"n": 2}]


def test_list_columns_roundtrip(tmp_path):
    s = Store(tmp_path)
    s.write("mentions", [dict(response_id="r1", workspace="ws", run_date=date(2026, 9, 21), engine="gemini",
                              mode="web", prompt_id="P1", brand_id="dk", brand_raw="Dot & Key", rank=1,
                              is_recommended=True, sentiment="positive", claims=["no white cast", "spf 50"],
                              source="both", extractor_version="ext-1")])
    assert s.rows("SELECT claims FROM mentions") == [{"claims": ["no white cast", "spf 50"]}]


def test_named_params(tmp_path):
    s = Store(tmp_path)
    s.write("citations", [row(), row(response_id="r2", workspace="other")])
    assert s.rows("SELECT response_id FROM citations WHERE workspace = $ws", {"ws": "ws"}) == [{"response_id": "r1"}]


def test_empty_table_is_queryable(tmp_path):
    assert Store(tmp_path).rows("SELECT count(*) AS n FROM funnel") == [{"n": 0}]


def test_s3_root_builds_keys_and_never_touches_the_filesystem(tmp_path, monkeypatch):
    s = Store("s3://shelfsight-lake/prod")
    assert s.is_remote
    assert s.partition("citations", "ws", date(2026, 9, 21)) == "s3://shelfsight-lake/prod/citations/workspace=ws/run_date=2026-09-21"
    assert s.glob("citations") == "s3://shelfsight-lake/prod/citations/**/*.parquet"
    monkeypatch.chdir(tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_local_root_keeps_filesystem_paths(tmp_path):
    s = Store(tmp_path / "lake")
    assert not s.is_remote
    assert s.partition("citations", "ws", date(2026, 9, 21)).endswith("lake/citations/workspace=ws/run_date=2026-09-21")


def test_unknown_column_rejected(tmp_path):
    with pytest.raises(ValueError, match="unknown columns"):
        Store(tmp_path).write("citations", [row(bogus=1)])
