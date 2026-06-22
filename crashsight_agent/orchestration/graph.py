"""LangGraph 状态机定义 — CrashSight Agent 的核心编排"""
import os
import sqlite3
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.sqlite import SqliteSaver
from .state import GraphState
from .nodes import route_node, clarify_node, act_node, observe_node, report_node


# Checkpointer 持久化路径
CHECKPOINT_DB = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    'data', 'checkpoints.sqlite'
)
os.makedirs(os.path.dirname(CHECKPOINT_DB), exist_ok=True)


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

    # 需要重试 → 回到 act
    if final_status == 'retry':
        return 'act'

    # 成功或错误 → 生成报告
    return 'report'


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

    # 编译，带 SQLite Checkpointer（状态持久化）
    checkpointer = SqliteSaver(sqlite3.connect(CHECKPOINT_DB, check_same_thread=False))
    return graph.compile(checkpointer=checkpointer)
