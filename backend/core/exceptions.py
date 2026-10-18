# backend/core/exceptions.py
# Landscape Agent 统一异常体系


class LandscapeAgentError(Exception):
    """所有自定义异常的基类。"""

    def __init__(self, message: str, agent_type: str = "", details: dict | None = None):
        super().__init__(message)
        self.agent_type = agent_type
        self.details = details or {}


# 兼容旧名（过渡期）
EduAgentBaseError = LandscapeAgentError


class LLMAPIError(LandscapeAgentError):
    """大模型 API 调用失败（可重试）。"""


class AgentExecutionError(LandscapeAgentError):
    """Agent 业务逻辑执行失败。"""


class PipelineError(LandscapeAgentError):
    """多 Agent Pipeline 失败。"""


class IntentRouteError(LandscapeAgentError):
    """意图识别路由失败。"""


class MilvusConnectionError(LandscapeAgentError):
    """Milvus 向量库连接失败（可重试）。"""


class FileParseError(LandscapeAgentError):
    """文件解析失败（PDF）。"""


class InvalidInputError(LandscapeAgentError):
    """用户输入不合法（不可重试）。"""


class AuthenticationError(LandscapeAgentError):
    """认证失败（不可重试）。"""
