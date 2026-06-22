"""结构化日志 — JSON 格式，按天分文件，支持按类型查询

日志文件: data/logs/agent_YYYYMMDD.jsonl
每行一条 JSON，格式:
{
    "ts": "2026-06-22T19:06:00",
    "level": "info",
    "category": "route",
    "event": "intent_matched",
    "data": {...},
    "session_id": "xxx",
    "duration_ms": 123
}
"""
import os
import json
import time
from datetime import datetime
from typing import Optional


LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'data', 'logs')


class AgentLogger:
    """Agent 结构化日志"""

    def __init__(self, session_id: str = ''):
        self.session_id = session_id
        os.makedirs(LOG_DIR, exist_ok=True)

    def _write(self, level: str, category: str, event: str, data: dict = None, duration_ms: int = None):
        """写一条日志"""
        record = {
            'ts': datetime.now().isoformat(timespec='seconds'),
            'level': level,
            'category': category,
            'event': event,
            'session_id': self.session_id,
        }
        if data:
            record['data'] = data
        if duration_ms is not None:
            record['duration_ms'] = duration_ms

        # 写文件（按天分）
        filename = f"agent_{datetime.now().strftime('%Y%m%d')}.jsonl"
        filepath = os.path.join(LOG_DIR, filename)
        with open(filepath, 'a', encoding='utf-8') as f:
            f.write(json.dumps(record, ensure_ascii=False) + '\n')

        # 同时打印到控制台（简短格式）
        print(f'[{category}] {event}: {json.dumps(data, ensure_ascii=False)[:100] if data else ""}')

    # ─── Route 相关 ───
    def log_route(self, query: str, intent: str, confidence: float, layer: str,
                  project_id: str = None, version: str = None,
                  start_date: str = None, end_date: str = None, duration_ms: int = None):
        """记录意图路由结果"""
        self._write('info', 'route', 'intent_resolved', {
            'query': query[:100],
            'intent': intent,
            'confidence': confidence,
            'layer': layer,
            'project_id': project_id,
            'version': version,
            'start_date': start_date,
            'end_date': end_date,
        }, duration_ms=duration_ms)

    # ─── 工具调用 ───
    def log_tool_call(self, tool_name: str, success: bool, duration_ms: int,
                      error: str = '', error_type: str = '', retried: int = 0):
        """记录工具调用"""
        level = 'info' if success else 'warn'
        self._write(level, 'tool', 'tool_call', {
            'tool': tool_name,
            'success': success,
            'error': error[:100] if error else '',
            'error_type': error_type,
            'retried': retried,
        }, duration_ms=duration_ms)

    # ─── LLM 调用 ───
    def log_llm_call(self, purpose: str, model: str, input_tokens: int = 0,
                     output_tokens: int = 0, duration_ms: int = 0, success: bool = True):
        """记录 LLM 调用"""
        self._write('info', 'llm', 'llm_call', {
            'purpose': purpose,
            'model': model,
            'input_tokens': input_tokens,
            'output_tokens': output_tokens,
            'success': success,
        }, duration_ms=duration_ms)

    # ─── 历史问题判定 ───
    def log_history_check(self, issue_id: str, key_frame: str, result: str,
                          reason: str = '', candidates_count: int = 0, duration_ms: int = 0):
        """记录历史问题判定"""
        self._write('info', 'history', 'history_check', {
            'issue_id': issue_id[:12],
            'key_frame': key_frame[:50],
            'result': result,  # match / mismatch / failed
            'reason': reason[:80],
            'candidates_count': candidates_count,
        }, duration_ms=duration_ms)

    # ─── 熔断器 ───
    def log_circuit_breaker(self, tool_name: str, action: str, failure_count: int = 0):
        """记录熔断器事件"""
        self._write('warn', 'circuit_breaker', action, {
            'tool': tool_name,
            'failure_count': failure_count,
        })

    # ─── 用户反馈 ───
    def log_feedback(self, issue_id: str, original_prediction: str, user_correction: str, user_reason: str):
        """记录用户反馈"""
        self._write('info', 'feedback', 'user_correction', {
            'issue_id': issue_id[:12],
            'original': original_prediction,
            'correction': user_correction,
            'reason': user_reason[:200],
        })

    # ─── 会话 ───
    def log_session_start(self, query: str):
        """记录会话开始"""
        self._write('info', 'session', 'query_start', {'query': query[:100]})

    def log_session_end(self, success: bool, duration_ms: int, answer_length: int = 0):
        """记录会话结束"""
        self._write('info', 'session', 'query_end', {
            'success': success,
            'answer_length': answer_length,
        }, duration_ms=duration_ms)

    # ─── Guard ───
    def log_guard_block(self, reason: str, project_id: str = '', version: str = '',
                        start_date: str = '', end_date: str = ''):
        """记录安全守卫拦截"""
        self._write('warn', 'guard', 'query_blocked', {
            'reason': reason,
            'project_id': project_id,
            'version': version,
            'start_date': start_date,
            'end_date': end_date,
        })


# ─── 全局单例 ───
_logger: Optional[AgentLogger] = None


def get_logger(session_id: str = '') -> AgentLogger:
    global _logger
    if _logger is None or (_logger.session_id != session_id and session_id):
        _logger = AgentLogger(session_id)
    return _logger
