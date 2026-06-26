"""LangGraph 状态机定义 — CrashSight Agent 的核心编排"""
import threading
import sqlite3
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.sqlite import SqliteSaver
from .state import GraphState
from .nodes import route_node, clarify_node, act_node, observe_node, report_node
from ..config import CHECKPOINT_DB_PATH, DATA_DIR

import os
os.makedirs(DATA_DIR, exist_ok=True)


def route_decide(state: dict) -> str:
    """Route 之后的条件路由"""
    missing = state.get('missing_params', [])
    intent = state.get('intent', '')

    # 无法理解 → 追问
    if intent == 'clarify':
        return 'clarify'

    # 缺参数 → 追问
    if missing:
        return 'clarify'

    # 参数齐全 → 执行
    return 'act'


def observe_decide(state: dict) -> str:
    """Observe 之后的条件路由"""
    final_status = state.get('final_status', 'ok')

    # 需要重试（原参数）→ 回到 act
    if final_status == 'retry':
        return 'act'

    # 需要恢复（新策略）→ 回到 act（act 节点会读取 recovery_strategy）
    if final_status == 'recover':
        return 'act'

    # 成功或错误 → 生成报告
    return 'report'


# ==================== 单例 Checkpointer 管理 ====================
# 全局共享一个 SQLite 连接（线程安全：check_same_thread=False + WAL 模式）
# 避免每次 build_graph() 都创建新连接导致泄漏

_checkpoint_conn: sqlite3.Connection | None = None
_checkpoint_lock = threading.Lock()


def _get_checkpoint_connection() -> sqlite3.Connection:
    """获取全局共享的 Checkpoint SQLite 连接（线程安全单例）"""
    global _checkpoint_conn
    if _checkpoint_conn is not None:
        return _checkpoint_conn

    with _checkpoint_lock:
        # 双重检查锁
        if _checkpoint_conn is not None:
            return _checkpoint_conn

        conn = sqlite3.connect(CHECKPOINT_DB_PATH, check_same_thread=False, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")  # 等待锁最多 10s
        _checkpoint_conn = conn
        return _checkpoint_conn


def close_checkpoint_connection():
    """关闭全局 Checkpoint 连接（程序退出时调用）"""
    global _checkpoint_conn
    with _checkpoint_lock:
        if _checkpoint_conn is not None:
            _checkpoint_conn.close()
            _checkpoint_conn = None


def build_graph():
    """构建 LangGraph 状态机

    流程:
        ┌─────┐
        │Route│ ← 入口：意图识别 + 参数解析
        └──┬──┘
           │
     ┌─────┼─────┐
     ▼           ▼
  ┌──────┐   ┌───┐
  │Clarify│   │Act│ ← 调用工具
  └──┬───┘   └─┬─┘
     │         │
     ▼         ▼
    END    ┌───────┐
           │Observe│ ← 结果检查
           └──┬────┘
              │
        ┌─────┼─────┐
        ▼           ▼
     ┌───┐     ┌──────┐
     │Act│     │Report│ ← 生成回答
     └───┘     └──┬───┘
      (重试)       │
                   ▼
                  END
    """
    graph = StateGraph(GraphState)

    # 添加节点
    graph.add_node('route', route_node)
    graph.add_node('clarify', clarify_node)
    graph.add_node('act', act_node)
    graph.add_node('observe', observe_node)
    graph.add_node('report', report_node)

    # 设置入口
    graph.set_entry_point('route')

    # 条件边: route → clarify / act
    graph.add_conditional_edges('route', route_decide, {
        'clarify': 'clarify',
        'act': 'act',
    })

    # clarify → END
    graph.add_edge('clarify', END)

    # act → observe
    graph.add_edge('act', 'observe')

    # 条件边: observe → act(重试) / report(完成)
    graph.add_conditional_edges('observe', observe_decide, {
        'act': 'act',
        'report': 'report',
    })

    # report → END
    graph.add_edge('report', END)

    # 编译，使用全局共享的 SQLite 连接（避免连接泄漏）
    # recursion_limit: 防止 observe↔act 环路死循环（正常流程最多 route→act→observe→act→observe→report = 6 步）
    conn = _get_checkpoint_connection()
    checkpointer = SqliteSaver(conn)
    return graph.compile(checkpointer=checkpointer, recursion_limit=15)
