"""Observe 节点 — 分析工具返回结果，决定继续还是终止"""
import time
from ...tools import execute_tool


def observe_node(state: dict) -> dict:
    """
    职责:
    1. 检查工具执行结果是否成功
    2. 对于 history_check 意图：如果拿到了堆栈，追加调用 LLM 历史判定
    3. 决定是否需要重试或追加调用
    """
    intent = state.get('intent', '')
    observations = state.get('observations', [])
    retry_count = state.get('retry_count', 0)
    project_id = state.get('project_id', '')
    issue_id = state.get('issue_id', '')

    # 检查是否全部失败
    all_failed = all(not obs.get('success') for obs in observations)
    if all_failed and retry_count < 2:
        # 全失败且有重试额度 → 标记需要重试
        last_error = observations[-1].get('error', '') if observations else ''
        return {
            'last_error': last_error,
            'retry_count': retry_count + 1,
            'final_status': 'retry',
        }

    if all_failed:
        # 重试耗尽
        return {
            'last_error': '工具调用全部失败，已重试2次',
            'final_status': 'error',
        }

    # history_check 意图：拿到堆栈后追加 LLM 对比
    if intent == 'history_check':
        stack_obs = next((o for o in observations if o['alias'] == 'stack' and o['success']), None)
        if stack_obs and stack_obs.get('data'):
            stack_data = stack_obs['data']
            call_stack = stack_data.get('callStack', '')
            if call_stack:
                print('[Observe] 堆栈获取成功，调用 LLM 历史问题判定...')
                # 获取异常名（从对话历史或 observations 中取）
                history_result = execute_tool('check_history_issue', {
                    'project_id': project_id,
                    'issue_id': issue_id,
                    'exp_stack': call_stack,
                    'exp_exception': '',
                })
                return {
                    'observations': [{
                        'tool': 'check_history_issue',
                        'alias': 'history',
                        'success': history_result.get('success', False),
                        'data': history_result.get('data'),
                        'error': history_result.get('error', ''),
                    }],
                    'final_status': 'ok',
                }
            else:
                return {
                    'last_error': '堆栈为空，无法判断历史问题',
                    'final_status': 'ok',  # 仍然继续到 report 节点输出解释
                }

    # 正常结束
    return {
        'final_status': 'ok',
    }
