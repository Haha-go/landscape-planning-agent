# scripts/ingest_kb.py
# 用法：
#   python scripts/ingest_kb.py knowledge_base/standards
#   python scripts/ingest_kb.py knowledge_base/standards/CJJ_48_desensitized_park_design.md \
#       --title "公园设计规范（脱敏教学稿）" --code "CJJ 48-教学"
#   python scripts/ingest_kb.py knowledge_base/sample   # 仅联通，不计入正式命中率

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.core.logger import configure_logging, get_logger
from backend.rag.embeddings import get_embedder
from backend.rag.ingest import ingest_directory, ingest_file, load_manifest
from backend.rag.milvus_client import connect_milvus
from backend.dependencies import AsyncSessionLocal

logger = get_logger(__name__)


async def main(target: str, title: str | None, code: str) -> None:
    configure_logging()
    connect_milvus()
    get_embedder().load()
    path = Path(target)
    if not path.is_absolute():
        path = (ROOT / path).resolve()
    if not path.exists():
        print(f"路径不存在: {path}")
        sys.exit(1)

    async with AsyncSessionLocal() as db:
        if path.is_file():
            # 单文件：CLI 优先，否则尝试同级 manifest
            meta = load_manifest(path.parent).get(path.name) or {}
            result = await ingest_file(
                path,
                db=db,
                title=title or meta.get("title") or path.stem,
                standard_code=code or meta.get("standard_code") or "",
            )
            results = [result]
        else:
            results = await ingest_directory(path, db=db)

    total = sum(r["chunk_count"] for r in results)
    print(f"入库完成：{len(results)} 个文档，共 {total} 个 chunk")
    for r in results:
        code_s = r.get("standard_code") or "-"
        print(
            f"  - {r.get('title')} [{code_s}] "
            f"({r['chunk_count']} chunks) doc_id={r.get('doc_id')}"
        )
    if total == 0:
        print("警告：chunk_count=0，可能是扫描件/无文本 PDF，请检查文件。")
        sys.exit(2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="知识库切块入库（PDF/MD/TXT）")
    parser.add_argument(
        "target",
        nargs="?",
        default="knowledge_base/standards",
        help="目录或单个文件路径（默认脱敏规范目录）",
    )
    parser.add_argument("--title", default=None, help="文档标题（仅单文件时生效）")
    parser.add_argument("--code", default="", help="规范代号 standard_code（仅单文件时生效）")
    args = parser.parse_args()
    asyncio.run(main(args.target, args.title, args.code))
