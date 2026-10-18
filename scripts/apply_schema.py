# scripts/apply_schema.py
# 对现有 Postgres 幂等应用景观表 DDL（不依赖 Docker 首次 init_db.sql）
# 用法：python scripts/apply_schema.py

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.core.logger import configure_logging, get_logger
from backend.db.migrations import run_migrations
from backend.config import get_settings

logger = get_logger(__name__)


async def main() -> None:
    configure_logging()
    settings = get_settings()
    print(
        f"Applying schema → {settings.db_host}:{settings.db_port}/{settings.db_name} "
        f"as {settings.db_user}"
    )
    await run_migrations()
    print("Schema migrations done.")


if __name__ == "__main__":
    asyncio.run(main())
