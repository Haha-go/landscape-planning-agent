# backend/rag/ingest.py
# 知识库切块入库：PDF/MD → BGE-M3 → Milvus + kb_documents

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import get_settings
from backend.core.logger import get_logger
from backend.rag.embeddings import get_embedder
from backend.rag.milvus_client import upsert_chunks


async def _resolve_doc_id(db: AsyncSession | None, source_path: str) -> str | None:
    """Reuse kb_documents.id for the same source_path so re-ingest replaces vectors."""
    if db is None:
        return None
    row = (
        await db.execute(
            text("SELECT id FROM kb_documents WHERE source_path = :path LIMIT 1"),
            {"path": source_path},
        )
    ).first()
    return str(row[0]) if row else None

logger = get_logger(__name__)


def _chunk_text(text_content: str, chunk_size: int, overlap: int) -> list[str]:
    text_content = re.sub(r"\n{3,}", "\n\n", text_content).strip()
    if not text_content:
        return []
    if len(text_content) <= chunk_size:
        return [text_content]
    chunks: list[str] = []
    start = 0
    while start < len(text_content):
        end = start + chunk_size
        chunks.append(text_content[start:end])
        if end >= len(text_content):
            break
        start = max(0, end - overlap)
    return chunks


def _read_file(path: Path) -> tuple[str, list[tuple[int, str]]]:
    """Return (full_text, [(page, page_text), ...])."""
    suffix = path.suffix.lower()
    if suffix == ".md" or suffix == ".txt":
        content = path.read_text(encoding="utf-8")
        return content, [(1, content)]
    if suffix == ".pdf":
        import fitz

        doc = fitz.open(path)
        pages: list[tuple[int, str]] = []
        parts: list[str] = []
        for i, page in enumerate(doc, start=1):
            t = page.get_text("text") or ""
            pages.append((i, t))
            parts.append(t)
        doc.close()
        return "\n".join(parts), pages
    raise ValueError(f"不支持的文件类型: {suffix}")


def load_manifest(dir_path: Path) -> dict[str, dict[str, str]]:
    """读取目录下 manifest.json，返回 filename → {title, standard_code}。"""
    manifest_path = Path(dir_path) / "manifest.json"
    if not manifest_path.is_file():
        return {}
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("ingest.manifest_invalid", path=str(manifest_path), error=str(e))
        return {}
    mapping: dict[str, dict[str, str]] = {}
    for item in data.get("documents") or []:
        rel = item.get("path") or ""
        if not rel:
            continue
        mapping[Path(rel).name] = {
            "title": item.get("title") or Path(rel).stem,
            "standard_code": item.get("standard_code") or "",
        }
    return mapping


def build_chunks_from_file(
    path: Path,
    title: str | None = None,
    standard_code: str = "",
    doc_id: str | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    settings = get_settings()
    doc_id = doc_id or str(uuid.uuid4())
    title = title or path.stem
    _, pages = _read_file(path)

    chunk_rows: list[dict[str, Any]] = []
    for page_no, page_text in pages:
        for i, chunk in enumerate(
            _chunk_text(page_text, settings.rag_chunk_size, settings.rag_chunk_overlap)
        ):
            # 从 chunk 首行粗提章节号，便于引用展示
            section = f"p{page_no}"
            first = (chunk.splitlines() or [""])[0].strip()
            m = re.match(r"^#{1,3}\s*(.+)$", first) or re.match(
                r"^(\d+(?:\.\d+)*)\s+", first
            )
            if m:
                section = m.group(1).strip()[:64]
            chunk_rows.append(
                {
                    "id": f"{doc_id}_{page_no}_{i}",
                    "text": chunk,
                    "doc_id": doc_id,
                    "title": title,
                    "section": section,
                    "page": page_no,
                    "standard_code": standard_code,
                }
            )
    return doc_id, chunk_rows


async def ingest_file(
    path: Path,
    db: AsyncSession | None = None,
    title: str | None = None,
    standard_code: str = "",
) -> dict[str, Any]:
    """切块 → 向量化 → upsert Milvus，可选写 kb_documents。

    重复入库同一 source_path 时复用 doc_id，并先删后插，避免重复 chunk。
    """
    path = Path(path).resolve()
    if not path.exists():
        raise FileNotFoundError(str(path))

    source_path = str(path)
    existing_id = await _resolve_doc_id(db, source_path)
    doc_id, chunk_rows = build_chunks_from_file(
        path,
        title=title,
        standard_code=standard_code,
        doc_id=existing_id,
    )
    if not chunk_rows:
        logger.warning("ingest.empty_file", path=source_path)
        return {"doc_id": doc_id, "chunk_count": 0, "title": title or path.stem}

    embedder = get_embedder()
    texts = [c["text"] for c in chunk_rows]
    batch_size = 16
    all_dense: list = []
    all_sparse: list = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        encoded = await embedder.encode(batch)
        all_dense.extend(encoded["dense"])
        all_sparse.extend(encoded["sparse"])

    for i, row in enumerate(chunk_rows):
        row["dense"] = all_dense[i]
        row["sparse"] = all_sparse[i]

    # replace_doc=True：按 doc_id 先删旧向量再 insert
    n = upsert_chunks(chunk_rows, replace_doc=True)
    logger.info("ingest.upserted", path=source_path, chunks=n, doc_id=doc_id)

    if db is not None:
        await db.execute(
            text(
                """
                INSERT INTO kb_documents (id, title, standard_code, source_path, chunk_count)
                VALUES (:id, :title, :code, :path, :cnt)
                ON CONFLICT (id) DO UPDATE SET
                    title = EXCLUDED.title,
                    standard_code = EXCLUDED.standard_code,
                    chunk_count = EXCLUDED.chunk_count,
                    updated_at = NOW()
                """
            ),
            {
                "id": doc_id,
                "title": title or path.stem,
                "code": standard_code or None,
                "path": source_path,
                "cnt": n,
            },
        )
        await db.commit()

    return {
        "doc_id": doc_id,
        "chunk_count": n,
        "title": title or path.stem,
        "standard_code": standard_code,
        "source_path": source_path,
    }


async def ingest_directory(dir_path: Path, db: AsyncSession | None = None) -> list[dict[str, Any]]:
    dir_path = Path(dir_path)
    meta = load_manifest(dir_path)
    results = []
    for p in sorted(dir_path.rglob("*")):
        if p.name == "manifest.json":
            continue
        if p.suffix.lower() in {".pdf", ".md", ".txt"} and p.is_file():
            info = meta.get(p.name) or {}
            results.append(
                await ingest_file(
                    p,
                    db=db,
                    title=info.get("title") or p.stem,
                    standard_code=info.get("standard_code") or "",
                )
            )
    return results
