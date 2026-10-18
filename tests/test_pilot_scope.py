# tests/test_pilot_scope.py
from pathlib import Path


def test_docs_separates_engineering_metrics_from_pilot():
    root = Path(__file__).resolve().parents[1]
    readme = (root / "README.md").read_text(encoding="utf-8")
    arch = (root / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")
    text = readme + "\n" + arch

    assert "成效" in text
    assert "试点" in text
    assert "规范智库" in text and "规划通" in text
    # 工程门槛与试点数字仍在文档中可追溯
    assert "95%" in text
    assert "P95" in text or "首 token" in text
    assert "Token" in text
    assert "evals/plan" in text or "evals/kb" in text
