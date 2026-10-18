# tests/fixtures/route_golden.py
# 路由黄金集：期望标签 qa / eval / other。规则见 route_intent（默认 auto）。

from __future__ import annotations


def golden_cases() -> list[dict]:
    cases: list[dict] = []

    def add(text: str, has_file: bool, expected: str, agent_mode: str = "auto") -> None:
        cases.append(
            {
                "text": text,
                "has_file": has_file,
                "expected": expected,
                "agent_mode": agent_mode,
            }
        )

    file_texts = [
        "你好",
        "绿地率一般怎么计算",
        "请评估",
        "evaluate",
        "",
        "帮我看看规范",
        "Please review plan",
        "今天天气怎么样",
        "上传方案",
        "儿童活动场地要注意什么",
        "EVALUATE",
        "公园里适合种什么树",
        "审查一下",
        "水体驳岸有哪些做法",
        "   ",
        "雨水花园是什么",
        "方案评估",
        "thanks",
        "打分",
        "无障碍坡道怎么把握",
    ]
    for text in file_texts:
        add(text, True, "eval")

    eval_speech = [
        "请评估这份说明",
        "帮我打分",
        "审查一下这个想法",
        "评价一下布局",
        "批改这版文本",
        "评分标准是什么",
        "我想上传方案",
        "规划方案还没传",
        "做一次方案评估",
        "方案评审什么时候开始",
        "evaluate this",
        "EVALUATE the site",
        "Please evaluate the landscape",
        "Please review plan",
        "please REVIEW PLAN today",
        "re-evaluate when you can",
        "帮我评价",
        "可以审查吗",
        "给这个想法打分",
        "我想做方案评审",
        "evaluate",
        "Review Plan",
        "请评估",
        "上传方案后再说",
        "没有文件，先审查要点",
    ]
    for text in eval_speech:
        add(text, False, "other")

    qa_texts = [
        "你好",
        "谢谢你的帮助",
        "今天天气怎么样",
        "公园里适合种什么树",
        "绿地率一般怎么计算",
        "水体驳岸有哪些做法",
        "儿童活动场地要注意什么",
        "园路宽度通常如何考虑",
        "雨水花园是什么",
        "透水铺装有什么作用",
        "生物多样性怎么在公园里体现",
        "夜景照明如何避免光污染",
        "无障碍坡道坡度一般怎么把握",
        "停车场绿化带怎么布置",
        "海绵城市在场地里怎么落地",
        "乔灌草层次怎么配",
        "古树保护有哪些常见措施",
        "滨水步道和防洪怎么兼顾",
        "社区公园服务半径怎么理解",
        "立体绿化适合哪些墙面",
        "土壤改良常用哪些方法",
        "灌溉方式怎么选",
        "标识系统怎么做才清楚",
        "活动草坪和林下空间怎么平衡",
        "冬季景观靠什么维持",
        "What is a rain garden?",
        "How wide is a typical park path?",
        "hello",
        "thanks for the note",
        "请介绍乡土植物",
        "驳岸和水位怎么配合",
        "座椅间距有什么经验",
        "林荫覆盖怎么估算",
        "场地排水坡度怎么取",
        "入口集散需要留多大",
        "quiet lawn and shade",
        "how does a bioswale work",
        "推荐几种耐湿乔木",
        "铺装接缝怎么留",
        "晨练场地怎么避开住宅",
    ]
    for text in qa_texts:
        add(text, False, "qa")

    for text in ("", " ", "   ", "\n", "\t\t"):
        add(text, False, "other")

    english_eval = [
        "evaluate",
        "Evaluate the drawing",
        "EVALUATE",
        "please evaluate",
        "Can you evaluate?",
        "review plan",
        "Review Plan",
        "PLEASE REVIEW PLAN",
        "time to evaluate",
        "we should review plan later",
    ]
    for text in english_eval:
        add(text, False, "other")

    for index in range(1, 16):
        add(f"补充询问 {index}：林下地被怎么选", False, "qa")

    return cases


def agent_mode_cases() -> list[dict]:
    """三档 agent_mode 对照用例（不计入 auto golden 准确率门槛）。"""
    cases: list[dict] = []

    def add(text: str, has_file: bool, mode: str, expected: str) -> None:
        cases.append(
            {
                "text": text,
                "has_file": has_file,
                "agent_mode": mode,
                "expected": expected,
            }
        )

    # kb：忽略评估话术，强制 qa；带文件 → other
    for text in (
        "请评估这份方案",
        "帮我打分",
        "绿地率一般怎么算",
        "你好",
        "evaluate this",
    ):
        add(text, False, "kb", "qa")
    add("请评估", True, "kb", "other")
    add("绿地率", True, "kb", "other")
    add("", False, "kb", "other")

    # plan：有文件 → eval；无文件 → other
    for text in ("请评估", "你好", "绿地率怎么算", ""):
        add(text, True, "plan", "eval")
        add(text, False, "plan", "other")

    # auto 抽样回归
    add("绿地率一般怎么算", False, "auto", "qa")
    add("请评估这份方案", False, "auto", "other")
    add("你好", True, "auto", "eval")

    return cases
