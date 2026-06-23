"""Observe 节点 — 结果评估 + 异常检测 + 自适应恢复

三层职责：
1. 基本检查：工具是否全部失败 → 重试
2. 结果合理性评估：检测异常模式（如堆栈全空、历史判定全新）
3. 自适应恢复：根据异常类型尝试不同恢复策略
"""
import time
from ...tools import execute_tool
from ...logging import get_logger


def observe_node(state: dict) -> dict:
    """
    Observe 节点主函数 — 从简单"检查成功失败"升级为"结果评估+反思+自适应恢复"
    
    决策路径：
    ├── 全部失败 + 有重试额度 → retry（回到 act 原参数重试）
    ├── 全部失败 + 无额度 → error
    ├── 检测到异常模式 + 有恢复策略 → recover（回到 act 用新策略）
    ├── history_check 追加判定 → ok
    └── 正常 → ok
    """
    intent = state.get('intent', '')
    observations = state.get('observations', [])
    retry_count = state.get('retry_count', 0)
    recover_count = state.get('recover_count', 0)
    project_id = state.get('project_id', '')
    issue_id = state.get('issue_id', '')
    logger = get_logger()

    # ═══════════ 第一层：基本失败检查 ═══════════
    all_failed = all(not obs.get('success') for obs in observations)
    if all_failed and retry_count < 2:
        last_error = observations[-1].get('error', '') if observations else ''
        logger._write('warn', 'observe', 'all_failed_retry', {
            'retry_count': retry_count + 1, 'error': last_error[:100]
        })
        return {
            'last_error': last_error,
            'retry_count': retry_count + 1,
            'final_status': 'retry',
        }

    if all_failed:
        logger._write('error', 'observe', 'all_failed_exhausted', {'retry_count': retry_count})
        return {
            'last_error': '工具调用全部失败，已重试2次',
            'final_status': 'error',
        }

    # ═══════════ 第二层：结果合理性评估（异常模式检测）═══════════
    if intent == 'crash_report' and recover_count < 2:
        anomaly = _detect_anomaly(observations, intent)
        if anomaly:
            recovery = _plan_recovery(anomaly, state)
            if recovery:
                logger._write('warn', 'observe', 'anomaly_detected', {
                    'anomaly': anomaly['type'],
                    'description': anomaly['description'],
                    'recovery_action': recovery['action'],
                    'recover_count': recover_count + 1,
                })
                print(f'[Observe] ⚠️ 异常检测: {anomaly["description"]}')
                print(f'[Observe] 🔄 恢复策略: {recovery["action"]}')
                return {
                    'recover_count': recover_count + 1,
                    'recovery_strategy': recovery,
                    'last_error': anomaly['description'],
                    'final_status': 'recover',
                }

    # ═══════════ 第三层：意图特定的追加处理 ═══════════
    # history_check 意图：拿到堆栈后追加 LLM 对比
    if intent == 'history_check':
        stack_obs = next((o for o in observations if o['alias'] == 'stack' and o['success']), None)
        if stack_obs and stack_obs.get('data'):
            stack_data = stack_obs['data']
            call_stack = stack_data.get('callStack', '')
            if call_stack:
                print('[Observe] 堆栈获取成功，调用 LLM 历史问题判定...')
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
                    'final_status': 'ok',
                }

    # 正常结束
    return {'final_status': 'ok'}


# ═══════════ 异常模式检测 ═══════════

def _detect_anomaly(observations: list, intent: str) -> dict:
    """
    检测结果中的异常模式
    
    返回: {'type': '异常类型', 'description': '描述', 'data': {...}} 或 None
    """
    # 找 top_issues 数据
    top_obs = next((o for o in observations if o.get('alias') == 'top_issues' and o.get('success')), None)
    if not top_obs or not top_obs.get('data'):
        return None

    top_issues = top_obs['data']
    if not isinstance(top_issues, list) or not top_issues:
        return None

    # ── 异常模式 1：堆栈全空 ──
    # 如果 top_issues 里的每个 issue 都没有拿到堆栈（通过 historyDetail 判断）
    issues_with_stack = sum(1 for iss in top_issues
                           if iss.get('historyDetail', {}).get('reason') != '堆栈为空')
    if issues_with_stack == 0 and len(top_issues) >= 5:
        return {
            'type': 'all_stacks_empty',
            'description': f'TOP{len(top_issues)} 问题的堆栈全部为空，可能是 API 参数不对',
            'data': {'total': len(top_issues), 'empty': len(top_issues)},
        }

    # ── 异常模式 2：堆栈获取率极低（<30%）──
    total = len(top_issues)
    stack_empty_count = sum(1 for iss in top_issues
                           if iss.get('historyDetail', {}).get('reason') == '堆栈为空')
    if total >= 5 and stack_empty_count / total > 0.7:
        return {
            'type': 'low_stack_rate',
            'description': f'堆栈获取率极低: {total - stack_empty_count}/{total}，大量 issue 可能是非标准异常类型',
            'data': {'total': total, 'empty': stack_empty_count},
        }

    # ── 异常模式 3：历史判定全部为新问题（且有堆栈）──
    issues_with_history_check = [iss for iss in top_issues if iss.get('historyDetail', {}).get('reason') != '堆栈为空']
    if len(issues_with_history_check) >= 5:
        all_new = all(not iss.get('isHistoryIssue') for iss in issues_with_history_check)
        if all_new:
            # 检查是否 reason 都是"正式服未搜到"→ 可能搜索范围太窄
            search_miss = sum(1 for iss in issues_with_history_check
                             if '未搜到' in (iss.get('historyDetail', {}).get('reason') or ''))
            if search_miss >= len(issues_with_history_check) * 0.8:
                return {
                    'type': 'all_new_search_miss',
                    'description': f'历史判定 {len(issues_with_history_check)}/{len(issues_with_history_check)} 全是新问题，正式服搜索可能范围太窄',
                    'data': {'checked': len(issues_with_history_check), 'search_miss': search_miss},
                }

    # ── 异常模式 4：趋势数据异常（崩溃率为0但有崩溃问题）──
    trend_obs = next((o for o in observations if o.get('alias') == 'trend' and o.get('success')), None)
    if trend_obs and trend_obs.get('data') and top_issues:
        trend = trend_obs['data']
        max_rate = trend.get('maxRate', 0)
        if max_rate == 0 and len(top_issues) > 0:
            total_crash = sum(iss.get('crashCount', 0) for iss in top_issues)
            if total_crash > 0:
                return {
                    'type': 'trend_zero_but_has_crashes',
                    'description': f'崩溃率为0但有{total_crash}次崩溃记录，趋势数据可能版本/时间范围不对',
                    'data': {'max_rate': max_rate, 'total_crash': total_crash},
                }

    return None


# ═══════════ 恢复策略规划 ═══════════

def _plan_recovery(anomaly: dict, state: dict) -> dict:
    """
    根据异常类型规划恢复策略
    
    返回: {'action': '描述', 'adjustments': {...}} 或 None
    """
    anomaly_type = anomaly['type']
    recover_count = state.get('recover_count', 0)

    if anomaly_type == 'all_stacks_empty':
        if recover_count == 0:
            # 第一次恢复：尝试用 keyStack 字段作为堆栈（top_issues 响应里可能自带）
            return {
                'action': '尝试使用 top_issues 自带的 keyStack 字段替代完整堆栈',
                'adjustments': {'use_key_stack_fallback': True},
            }
        elif recover_count == 1:
            # 第二次恢复：跳过堆栈获取和历史判定，只输出基础数据
            return {
                'action': '跳过堆栈获取，直接使用已有数据生成报告',
                'adjustments': {'skip_stack_and_history': True},
            }

    elif anomaly_type == 'low_stack_rate':
        if recover_count == 0:
            return {
                'action': '对堆栈为空的 issue 尝试用 keyStack 兜底',
                'adjustments': {'use_key_stack_fallback': True},
            }

    elif anomaly_type == 'all_new_search_miss':
        if recover_count == 0:
            # 搜索范围太窄 → 尝试扩大版本通配符
            return {
                'action': '扩大正式服搜索范围（去掉版本限制，用 * 通配）',
                'adjustments': {'broaden_search_version': True},
            }

    elif anomaly_type == 'trend_zero_but_has_crashes':
        if recover_count == 0:
            return {
                'action': '趋势数据版本范围与实际不符，尝试用 -1（全版本）重拉趋势',
                'adjustments': {'trend_all_versions': True},
            }

    return None
