# backend/agents/eval/schemas.py
# 规划方案抽取与五维评分 Schema

from pydantic import BaseModel, Field, field_validator


SUMMARY_MAX_CHARS = 120
ISSUES_MAX = 20


def clamp_score(value: object) -> float:
    """把分数限制在 0–100。无法解析时记 0。"""
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    if number < 0:
        return 0.0
    if number > 100:
        return 100.0
    return number


class PlanExtractSchema(BaseModel):
    """结构化抽取结果。"""

    project_name: str = Field(default="", description="项目名称")
    project_overview: str = Field(default="", description="项目概况")
    planning_concept: str = Field(default="", description="规划理念")
    functional_zones: list[str] = Field(default_factory=list, description="功能分区")
    green_water_indicators: str = Field(default="", description="绿地/水体等指标描述")
    plants_ecology: str = Field(default="", description="植物配置与生态措施")
    traffic: str = Field(default="", description="交通与流线")
    investment_operation: str = Field(default="", description="投资与运营管理")
    cited_standards: list[str] = Field(default_factory=list, description="引用的规范条款")
    other_notes: str = Field(default="", description="其他要点")


class DimensionScore(BaseModel):
    """单维评分。"""

    score: float = Field(description="0-100 分")
    evidence: list[str] = Field(default_factory=list, description="依据摘录")
    risks: list[str] = Field(default_factory=list, description="风险点")
    suggestions: list[str] = Field(default_factory=list, description="改进建议")

    @field_validator("score", mode="before")
    @classmethod
    def _clamp_score(cls, value: object) -> float:
        return clamp_score(value)


# 五维名称
DIM_CONCEPT = "planning_concept"
DIM_COMPLIANCE = "compliance"
DIM_ECOLOGY = "ecology"
DIM_LAYOUT = "layout"
DIM_ECONOMY = "economy"

DIM_LABELS = {
    DIM_CONCEPT: "规划理念",
    DIM_COMPLIANCE: "规范符合性",
    DIM_ECOLOGY: "生态可持续",
    DIM_LAYOUT: "功能布局",
    DIM_ECONOMY: "经济可行性与运营管理",
}
