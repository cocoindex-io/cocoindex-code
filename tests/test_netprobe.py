from cocoindex_code.netprobe import probe_pgvector_extension


def test_probe_pgvector_returns_false_on_invalid_dsn() -> None:
    # Using an obviously invalid DSN and a short timeout should return False.
    assert probe_pgvector_extension("postgresql://invalid:bad@127.0.0.1:54321/db", timeout=1) is False
