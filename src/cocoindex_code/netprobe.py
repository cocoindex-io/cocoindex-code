"""Network probes for cocoindex-code.

Currently provides a small helper to probe whether the pgvector extension is
installed in a Postgres DSN using psycopg2 with a configurable connect timeout.
The function is intentionally tolerant of missing psycopg2 so callers that
invoke it in environments without the dependency can still import the module.
"""

from __future__ import annotations

import os
from typing import Tuple


def probe_pgvector_extension(dsn: str, timeout: int = 5) -> bool:
    """Return True if the Postgres DSN is reachable and has the pgvector ext.

    The function returns False on any error (unreachable, missing dependency,
    missing extension). Importing psycopg2 is deferred to avoid hard import
    failures in test or consumer environments that don't have the driver.
    """
    try:
        import psycopg2
    except Exception:
        # psycopg2 not available — cannot probe
        return False

    if not dsn:
        return False

    try:
        conn = psycopg2.connect(dsn, connect_timeout=timeout)
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM pg_extension WHERE extname='vector'")
        row = cur.fetchone()
        conn.close()
        return bool(row)
    except Exception:
        return False


if __name__ == "__main__":
    import sys

    dsn = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("COCOINDEX_DATABASE_URL", "")
    timeout = int(sys.argv[2]) if len(sys.argv) > 2 else int(os.environ.get("PGCONNECT_TIMEOUT", "5"))
    ok = probe_pgvector_extension(dsn, timeout)
    sys.exit(0 if ok else 1)
