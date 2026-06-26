"""LangGraph 状态定义"""
from typing import TypedDict, Optional, Annotated
from operator import add


class GraphState(TypedDict):
    """Agent 状态机的全局状态"""

    # ─── 输入 ───
    query: str                              # 用户当前输入
    session_history: list                   # 多轮对话历史

    # ─── Route 输出 ───
    intent: str                             # 主意图（第一个执行的）
    confidence: float                       # 主意图置信度
    intents: list                           # 多意图列表 [{'intent': ..., 'confidence': ...}, ...]
    deferred_intents: list                  # 被推迟的意图（超出3个上限的）

    # ─── 参数解析 ───
    project_id: Optional[str]               # 项目 ID
    version: Optional[str]                  # 版本号
    start_date: Optional[str]               # 开始日期 YYYYMMDD
    end_date: Optional[str]                 # 结束日期 YYYYMMDD
    issue_id: Optional[str]                 # 特定 issue（追问场景）
    missing_params: list                    # 缺失参数列表

    # ─── Act/Observe ───
    tool_calls: Annotated[list, add]        # 工具调用记录（累加）
    observations: Annotated[list, add]      # 工具结果记录（累加）
    step_count: int                         # 当前步数
    last_error: Optional[str]              # 最近一次错误
    retry_count: int                        # 重试次数
    recover_count: int                      # 自适应恢复次数
    recovery_strategy: Optional[dict]       # 当前恢复策略

    # ─── 终态输出 ───
    answer: str                             # 最终回答文本
    report_markdown: Optional[str]          # Markdown 报告
    clarify_question: Optional[str]         # 追问问题

    # ─── 元信息 ───
    final_status: str                       # ok / clarify / error / budget_exceeded
