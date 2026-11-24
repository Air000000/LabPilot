"""Read-only, secret-safe probes for the interview-only startup script."""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import ProxyHandler, build_opener

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.ext.asyncio import create_async_engine

from labpilot.db.readiness import DatabaseReadinessProbe


def safe_detail(value: object) -> str:
    if value is None:
        return "none"
    if isinstance(value, str) and re.fullmatch(
        r"[A-Za-z][A-Za-z0-9_]*(?:Error|Exception)|current=(?:None|[A-Za-z0-9_]+), "
        r"heads=\[(?:'[A-Za-z0-9_]+'(?:, )?)*\]",
        value,
    ):
        return value
    return "redacted_unexpected_detail"


async def database() -> int:
    engine = create_async_engine(os.environ["LABPILOT_DATABASE_URL"], connect_args={"timeout": 3})
    try:
        result = await asyncio.wait_for(DatabaseReadinessProbe(engine, "alembic.ini").check(), 5)
        print(
            f"database={result.database} migration={result.migration} "
            f"detail={safe_detail(result.detail)}"
        )
        heads = sorted(ScriptDirectory.from_config(Config("alembic.ini")).get_heads())
        match = re.match(r"current=([A-Za-z0-9_]+)", result.detail or "")
        current = heads[0] if result.ready else match.group(1) if match else "unknown"
        print(f"alembic current={current} head={','.join(heads)}")
        return 0 if result.database == "ok" else 1
    finally:
        await engine.dispose()


def ready() -> int:
    try:
        response = build_opener(ProxyHandler({})).open(
            "http://127.0.0.1:18001/health/ready", timeout=3
        )
    except HTTPError as exc:
        response = exc
    with response:
        code = response.code
        payload = json.loads(response.read(8192))
    status = payload.get("status")
    db = payload.get("database")
    migration = payload.get("migration")
    status = status if status in {"ready", "not_ready"} else "not_ready"
    db = db if db in {"ok", "unavailable"} else "unavailable"
    migration = migration if migration in {"head", "behind", "unknown"} else "unknown"
    print(
        f"http={code} status={status} database={db} migration={migration} "
        f"detail={safe_detail(payload.get('detail'))}"
    )
    return 0 if code == 200 and status == "ready" and db == "ok" and migration == "head" else 1


def main() -> int:
    try:
        mode = sys.argv[1]
        if mode == "url":
            password = quote(Path(sys.argv[2]).read_text().strip(), safe="")
            print(
                f"postgresql+asyncpg://labpilot_operator:{password}"
                "@127.0.0.1:55436/labpilot_operator_interview"
            )
            return 0
        if mode == "database":
            return asyncio.run(database())
        if mode == "ready":
            return ready()
        raise ValueError("unsupported probe")
    except Exception as exc:
        print(f"database=unavailable migration=unknown detail={type(exc).__name__}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
