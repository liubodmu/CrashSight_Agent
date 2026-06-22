"""SSE 流式事件系统"""
import json
import time
import asyncio
from enum import Enum
from typing import Optional, Callable
from dataclasses import dataclass, field


class EventType(str, Enum):
    """Agent 执行过程中的事件类型"""
    # 整体流程
    AGENT_START = 'agent_start'          # Agent 开始处理
    AGENT_END = 'agent_end'              # Agent 处理完成

    # 节点流转
    NODE_ENTER = 'node_enter'            # 进入某个节点
    NODE_EXIT = 'node_exit'              # 离开某个节点

    # 工具调用
    TOOL_START = 'tool_start'            # 开始调用工具
    TOOL_SUCCESS = 'tool_success'        # 工具调用成功
    TOOL_ERROR = 'tool_error'            # 工具调用失败

    # 历史问题判定
    HISTORY_SEARCH = 'history_search'    # 正在搜索候选
    HISTORY_COMPARE = 'history_compare'  # LLM 正在对比
    HISTORY_RESULT = 'history_result'    # 判定结果

    # 最终输出
    REPORT_CHUNK = 'report_chunk'        # 报告片段（逐步输出）
    ANSWER = 'answer'                    # 最终回答

    # 异常
    ERROR = 'error'                      # 错误
    WARNING = 'warning'                  # 警告


@dataclass
class StreamEvent:
    """单条流式事件"""
    type: EventType
    message: str                         # 给用户看的文字
    data: Optional[dict] = None          # 结构化数据
    timestamp: float = field(default_factory=time.time)
    node: str = ''                       # 当前节点名

    def to_sse(self) -> str:
        """转为 SSE 格式字符串"""
        payload = {
            'type': self.type.value,
            'message': self.message,
            'node': self.node,
            'timestamp': self.timestamp,
        }
        if self.data:
            payload['data'] = self.data
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    def to_dict(self) -> dict:
        return {
            'type': self.type.value,
            'message': self.message,
            'node': self.node,
            'data': self.data,
            'timestamp': self.timestamp,
        }


class EventEmitter:
    """事件发射器 — 收集 Agent 执行过程中的所有事件
    
    两种使用方式:
    1. 同步: emit() 存入队列，事后一次性取出
    2. 异步: emit() 同时推入 asyncio.Queue，前端实时消费
    """

    def __init__(self):
        self.events: list[StreamEvent] = []
        self._async_queue: Optional[asyncio.Queue] = None
        self._listeners: list[Callable] = []

    def enable_async(self) -> asyncio.Queue:
        """启用异步模式，返回 Queue 供 SSE 消费"""
        self._async_queue = asyncio.Queue()
        return self._async_queue

    def emit(self, event_type: EventType, message: str, data: dict = None, node: str = ''):
        """发射一个事件"""
        event = StreamEvent(type=event_type, message=message, data=data, node=node)
        self.events.append(event)

        # 异步推送
        if self._async_queue:
            try:
                self._async_queue.put_nowait(event)
            except asyncio.QueueFull:
                pass  # 队列满了丢弃（不阻塞）

        # 同步监听器
        for listener in self._listeners:
            try:
                listener(event)
            except:
                pass

    def on_event(self, listener: Callable):
        """注册事件监听器（同步回调）"""
        self._listeners.append(listener)

    def emit_tool_start(self, tool_name: str, issue_info: str = '', node: str = 'act'):
        """快捷: 工具开始"""
        msg = f'🔧 调用 {tool_name}'
        if issue_info:
            msg += f' ({issue_info})'
        self.emit(EventType.TOOL_START, msg, {'tool': tool_name}, node=node)

    def emit_tool_success(self, tool_name: str, summary: str = '', node: str = 'act'):
        """快捷: 工具成功"""
        msg = f'✓ {tool_name} 完成'
        if summary:
            msg += f' — {summary}'
        self.emit(EventType.TOOL_SUCCESS, msg, {'tool': tool_name}, node=node)

    def emit_tool_error(self, tool_name: str, error: str = '', node: str = 'act'):
        """快捷: 工具失败"""
        self.emit(EventType.TOOL_ERROR, f'✗ {tool_name} 失败: {error[:50]}', {'tool': tool_name, 'error': error}, node=node)

    def emit_node(self, node_name: str, entering: bool = True):
        """快捷: 节点进入/离开"""
        node_labels = {
            'route': '🧭 意图识别',
            'clarify': '❓ 追问确认',
            'act': '⚡ 执行工具',
            'observe': '👁️ 结果分析',
            'report': '📝 生成报告',
        }
        label = node_labels.get(node_name, node_name)
        if entering:
            self.emit(EventType.NODE_ENTER, f'{label}...', node=node_name)
        else:
            self.emit(EventType.NODE_EXIT, f'{label} 完成', node=node_name)

    def get_all(self) -> list[dict]:
        """获取所有事件（字典格式）"""
        return [e.to_dict() for e in self.events]

    def clear(self):
        """清空事件"""
        self.events = []


# 全局单例（供各节点使用）
_current_emitter: Optional[EventEmitter] = None


def get_emitter() -> Optional[EventEmitter]:
    return _current_emitter


def set_emitter(emitter: Optional[EventEmitter]):
    global _current_emitter
    _current_emitter = emitter
