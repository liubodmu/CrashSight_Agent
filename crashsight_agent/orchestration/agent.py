"""CrashSight Agent — 基于 LangGraph 状态机 + Checkpointer 持久化"""
import uuid
import time
import logging
import threading
from .graph import build_graph
from ..context import WindowManager, HistoryCompressor
from ..logging import get_logger

_logger = logging.getLogger(__name__)


class CrashSightAgent:
    """CrashSight 崩溃分析 Agent

    基于 LangGraph 状态机编排:
        Route → Clarify / Act → Observe → Report

    特性:
    - 多轮对话: session_history 在 GraphState 中跨轮次传递
    - 状态持久化: SqliteSaver Checkpointer，程序重启可恢复对话
    - 会话隔离: 每个 thread_id 独立的状态流
    - 线程安全: 会话状态通过 Lock 保护，支持并发请求
    """

    def __init__(self, thread_id: str = None):
        self.graph = build_graph()
        self.thread_id = thread_id or str(uuid.uuid4())
        self.session_history = []
        self.last_observations = []
        self._lock = threading.Lock()  # 保护会话状态的并发访问

        # Context 工程
        self.window_manager = WindowManager(max_total=8000, max_history=4000, max_tool_result=2000)
        self.compressor = HistoryCompressor(threshold_tokens=3000, keep_recent=3)

    def chat(self, user_message: str) -> str:
        """处理一次用户消息，返回 Agent 回答（线程安全）"""
        with self._lock:
            return self._chat_inner(user_message)

    def _chat_inner(self, user_message: str) -> str:
        """实际处理逻辑（已在 lock 内）"""
        # Context 工程: 检查历史是否需要压缩
        self.session_history = self.compressor.maybe_compress(self.session_history)

        # 日志: 请求开始
        logger = get_logger(self.thread_id)
        logger.new_trace()
        logger.log_session_start(user_message)
        start_time = time.time()

        # token 预算检查
        budget = self.window_manager.get_budget_status(self.session_history)
        if budget['needs_compression']:
            _logger.warning(f'历史 token 使用 {budget["history_usage_pct"]}%，接近上限')

        # 构建初始状态
        initial_state = {
            'query': user_message,
            'session_history': self.session_history,
            'intent': '',
            'confidence': 0.0,
            'project_id': None,
            'version': None,
            'start_date': None,
            'end_date': None,
            'issue_id': None,
            'missing_params': [],
            'tool_calls': [],
            'observations': self.last_observations if self._is_followup(user_message) else [],
            'step_count': 0,
            'last_error': None,
            'retry_count': 0,
            'answer': '',
            'report_markdown': None,
            'clarify_question': None,
            'final_status': '',
        }

        # 运行状态机（带 thread_id，Checkpointer 自动存盘）
        config = {'configurable': {'thread_id': self.thread_id}}
        result = self.graph.invoke(initial_state, config=config)

        # 提取回答
        answer = result.get('answer', '') or result.get('clarify_question', '出了点问题，请重试。')

        # 日志: 请求结束
        duration = int((time.time() - start_time) * 1000)
        logger.log_session_end(success=bool(answer and len(answer) > 10),
                               duration_ms=duration, answer_length=len(answer))

        # 更新会话历史
        self.session_history.append({
            'user': user_message,
            'assistant': answer[:200],
        })

        # 保存工具结果供追问
        if result.get('observations'):
            self.last_observations = result['observations']

        return answer

    def _is_followup(self, message: str) -> bool:
        """判断是否为追问（基于上一轮结果 + 关键词辅助）

        核心逻辑：必须有上一轮的工具执行结果，且当前消息引用了上一轮内容。
        避免首次对话就误判为追问。
        """
        # 没有上一轮结果，不可能是追问
        if not self.last_observations:
            return False

        # 没有对话历史，不可能是追问
        if not self.session_history:
            return False

        # 有上一轮结果时，检查追问意图关键词
        followup_keywords = [
            'top1', 'top2', 'top3', 'top4', 'top5',
            '第一个', '第二个', '第三个',
            '历史问题', '正式服有没有', '正式服有吗',
            '堆栈', '详情', '详细', 'tapd',
            '那个', '这个', '它', '刚才',
        ]
        msg_lower = message.lower()
        return any(kw in msg_lower for kw in followup_keywords)

    def reset(self):
        """重置对话（新建 thread）"""
        self.thread_id = str(uuid.uuid4())
        self.session_history = []
        self.last_observations = []

    def resume(self, thread_id: str):
        """恢复历史会话"""
        self.thread_id = thread_id
        _logger.info(f'恢复会话: {thread_id}')
        # Checkpointer 会自动从 SQLite 加载该 thread 的状态
