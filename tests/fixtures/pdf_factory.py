# tests/fixtures/pdf_factory.py
# 运行时生成可选中文字的方案 PDF 与几乎无字的扫描件

from __future__ import annotations

from pathlib import Path


def _fitz():
    import fitz
    return fitz


def write_text_pdf(path: Path, text: str) -> None:
    fitz = _fitz()
    doc = fitz.open()
    rect = fitz.Rect(48, 48, 548, 800)
    remaining = text
    while remaining:
        page = doc.new_page()
        written = page.insert_textbox(rect, remaining[:4000], fontsize=11)
        if written < 0:
            remaining = remaining[1500:]
        else:
            remaining = remaining[4000:]
        if len(doc) > 8:
            break
    doc.save(path)
    doc.close()


def write_scan_pdf(path: Path) -> None:
    """空白页，抽取字符数低于失败阈值。"""
    fitz = _fitz()
    doc = fitz.open()
    doc.new_page()
    doc.save(path)
    doc.close()


def build_plan_fixtures(directory: Path) -> tuple[list[Path], list[Path]]:
    directory.mkdir(parents=True, exist_ok=True)
    texts = _plan_texts()
    text_paths: list[Path] = []
    for index, body in enumerate(texts, start=1):
        path = directory / f"plan_{index:02d}.pdf"
        write_text_pdf(path, body)
        text_paths.append(path)
    scans: list[Path] = []
    for index in range(1, 4):
        path = directory / f"scan_{index}.pdf"
        write_scan_pdf(path)
        scans.append(path)
    return text_paths, scans


def _plan_texts() -> list[str]:
    base = (
        "滨水公园规划方案。项目位于城市东侧滨河地带，用地约12公顷。"
        "规划理念强调慢行优先与社区共享。功能分区包括入口广场、活动草坪、"
        "儿童活动、安静休息、滨水栈道与管理用房。绿地率按35%控制，水体面积约占8%。"
        "植物配置以乡土乔木为骨干，并设置雨水花园。交通组织人车分流，主园路宽3米。"
        "投资估算分近期与远期，运营由街道与专业团队共同维护。"
        "引用规范：GB 51192 公园设计规范，CJJ 82 园林绿化工程施工及验收规范。"
    )
    variants = []
    for index in range(1, 21):
        extra = ""
        if index % 5 == 0:
            extra = "本方案未提供投资估算与运营计划，相关字段留空。"
        elif index % 5 == 1:
            extra = "引用规范还包括 GB 50420 城市绿地设计规范。水体驳岸采用自然式。"
        elif index % 5 == 2:
            extra = "文本较长。" + ("园路、铺装与照明需与防洪标高协调。" * 80)
        elif index % 5 == 3:
            extra = "缺少植物名录与交通流量，仅保留分区示意。"
        else:
            extra = "补充海绵设施：透水铺装、下沉绿地与植草沟。"
        variants.append(f"{base}\n方案编号 {index}。{extra}")
    return variants
