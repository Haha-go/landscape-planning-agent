# backend/parsers/pdf.py
# PyMuPDF 文本抽取（本轮不做 OCR）

from __future__ import annotations

import time
from pathlib import Path

from backend.core.exceptions import FileParseError
from backend.core.logger import get_logger
from backend.core.metrics import record_metric

logger = get_logger(__name__)


def extract_pdf_text(path: str | Path) -> dict:
    """抽取 PDF 文本与页码。扫描件（几乎无字）抛 FileParseError。"""
    started = time.perf_counter()
    try:
        payload = _extract_pdf_text(path)
    except Exception as exc:
        record_metric(
            tool="parse",
            step="parse",
            success=False,
            latency_ms=(time.perf_counter() - started) * 1000,
            detail={"error": type(exc).__name__},
        )
        raise
    record_metric(
        tool="parse",
        step="parse",
        success=True,
        latency_ms=(time.perf_counter() - started) * 1000,
        detail={"pages": payload["page_count"], "chars": payload["char_count"]},
    )
    return payload


def _extract_pdf_text(path: str | Path) -> dict:
    path = Path(path)
    if not path.exists():
        raise FileParseError(f"文件不存在: {path}", agent_type="eval")
    if path.suffix.lower() != ".pdf":
        raise FileParseError(f"仅支持 PDF，收到: {path.suffix}", agent_type="eval")

    try:
        import fitz
    except ImportError as e:
        raise FileParseError("pymupdf 未安装", agent_type="eval") from e

    try:
        doc = fitz.open(path)
    except Exception as e:
        raise FileParseError(f"无法打开 PDF: {e}", agent_type="eval") from e

    pages: list[dict] = []
    total_chars = 0
    try:
        for i, page in enumerate(doc, start=1):
            text = page.get_text("text") or ""
            total_chars += len(text.strip())
            pages.append({"page": i, "text": text})
    finally:
        doc.close()

    if total_chars < 50:
        raise FileParseError(
            "PDF 几乎无可抽取文本，可能是扫描件。本轮不支持 OCR，请上传可选中文本的 PDF。",
            agent_type="eval",
        )

    full_text = "\n\n".join(f"[第{p['page']}页]\n{p['text']}" for p in pages)
    logger.info("pdf.extracted", path=str(path), pages=len(pages), chars=total_chars)
    return {
        "page_count": len(pages),
        "char_count": total_chars,
        "pages": pages,
        "full_text": full_text,
    }
