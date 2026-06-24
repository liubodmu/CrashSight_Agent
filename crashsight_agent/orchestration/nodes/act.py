"""Act 节点 — 根据意图调用工具（带安全守卫 + 流式事件 + 并行执行）"""
import json
import time
import asyncio
from ...tools import execute_tool
from ...tools.guard import check_query_safety
from ...tools.parallel_executor import parallel_process_issues, parallel_fetch_tapd
from ...config import PROJECTS
from ...streaming.events import get_emitter, EventType
from ...logging import get_logger


# ==================== 执行计划模板 ====================
# 比 INTENT_PLANS 更细粒度：同一意图根据上下文选不同模板
#
# 选择逻辑：select_plan(intent, query, context) → plan_name
# 每个模板定义执行步骤和是否需要完整流程（并行堆栈+历史判定）

PLAN_TEMPLATES = {
    # ── crash_report 意图的子计划 ──
    'full_report': {
        'desc': '完整崩溃报告（趋势+TOP10+堆栈+历史+TAPD）',
        'steps': [
            {'tool': 'get_crash_trend', 'alias': 'trend'},
            {'tool': 'get_top_issues', 'alias': 'top_issues'},
        ],
        'need_full_pipeline': True,  # 需要后续并行堆栈+历史判定+TAPD
    },
    'top_only': {
        'desc': '只看 TOP 列表（不做历史判定，快速出结果）',
        'steps': [
            {'tool': 'get_crash_trend', 'alias': 'trend'},
            {'tool': 'get_top_issues', 'alias': 'top_issues'},
        ],
        'need_full_pipeline': False,  # 跳过堆栈+历史判定
    },
    'quick_status': {
        'desc': '快速状态检查（只看趋势+TAPD，不拉堆栈）',
        'steps': [
            {'tool': 'get_crash_trend', 'alias': 'trend'},
            {'tool': 'get_top_issues', 'alias': 'top_issues'},
        ],
        'need_full_pipeline': False,
        'tapd_only': True,  # 只拉 TAPD 不做历史判定
    },

    # ── trend_query 意图 ──
    'trend_only': {
        'desc': '只看崩溃率趋势',
        'steps': [
            {'tool': 'get_crash_trend', 'alias': 'trend'},
        ],
        'need_full_pipeline': False,
    },

    # ── issue_detail 意图 ──
    'deep_dive': {
        'desc': '深入分析某个 issue（堆栈+历史+TAPD）',
        'steps': [
            {'tool': 'get_issue_full_stack', 'alias': 'stack'},
        ],
        'need_full_pipeline': False,
    },

    # ── history_check 意图 ──
    'history_check': {
        'desc': '判断是否历史问题',
        'steps': [
            {'tool': 'get_issue_full_stack', 'alias': 'stack'},
        ],
        'need_full_pipeline': False,
    },

    # ── compare 意图 ──
    'compare_periods': {
        'desc': '对比两个时间段的崩溃',
        'steps': [
            {'tool': 'get_crash_trend', 'alias': 'trend'},
            {'tool': 'get_top_issues', 'alias': 'top_issues'},
        ],
        'need_full_pipeline': False,
    },
}


def select_plan(intent: str, query: str, version: str = '', issue_id: str = '') -> str:
    """根据意图+上下文选择执行计划模板
    
    选择逻辑（不调 LLM，纯规则）：
    - crash_report + 有具体版本 → full_report（做完整分析）
    - crash_report + 无版本 / 全版本 → top_only（不做历史判定，版本太泛结果不准）
    - crash_report + query 中提到 TAPD/状态/跟进 → quick_status
    - trend_query → trend_only
    - issue_detail → deep_dive
    - history_check → history_check
    - compare → compare_periods
    """
    import re

    if intent == 'crash_report':
        # 用户问 TAPD 状态/处理情况
        if re.search(r'tapd|状态|处理|跟进|谁在|分配', query.lower()):
            return 'quick_status'
        # 有具体版本 → 做完整流程（历史判定有意义）
        if version and version != '-1' and version != '':
            return 'full_report'
        # 无版本/全版本 → 只出 TOP 列表
        return 'top_only'

    elif intent == 'trend_query':
        return 'trend_only'

    elif intent == 'issue_detail':
        return 'deep_dive'

    elif intent == 'history_check':
        return 'history_check'

    elif intent == 'compare':
        return 'compare_periods'

    # 默认
    return 'full_report'


# 兼容旧代码的 INTENT_PLANS（通过 select_plan 选择后不再直接使用）
INTENT_PLANS = {
    'crash_report': PLAN_TEMPLATES['full_report']['steps'],
    'trend_query': PLAN_TEMPLATES['trend_only']['steps'],
    'issue_detail': PLAN_TEMPLATES['deep_dive']['steps'],
    'history_check': PLAN_TEMPLATES['history_check']['steps'],
}


def act_node(state: dict) -> dict:
    """执行工具调用（执行前经过安全守卫检查）"""
    intent = state.get('intent', 'crash_report')
    project_id = state.get('project_id', 'android_exp')
    version = state.get('version', '-1')
    start_date = state.get('start_date', '')
    end_date = state.get('end_date', '')
    issue_id = state.get('issue_id', '')
    step_count = state.get('step_count', 0)
    logger = get_logger()

    logger._write('info', 'act', 'act_start', {
        'intent': intent, 'project_id': project_id, 'version': version,
        'start_date': start_date, 'end_date': end_date, 'issue_id': issue_id,
    })

    # ─── 安全守卫：拦截不合理查询 ───
    if intent in ('crash_report', 'trend_query', 'compare'):
        guard_result = check_query_safety(project_id, version, start_date, end_date)

        if not guard_result.get('allowed'):
            reason = guard_result.get('reason', '')
            suggestion = guard_result.get('suggestion', '')
            print(f'[Guard] ⛔ 拦截: {reason}')
            return {
                'tool_calls': [],
                'observations': [{
                    'tool': '_guard',
                    'alias': 'guard_block',
                    'success': False,
                    'data': None,
                    'error': f'查询被拦截: {reason}。{suggestion}',
                }],
                'step_count': step_count + 1,
                'last_error': f'{reason}。{suggestion}',
                'final_status': 'error',
            }

        if guard_result.get('warning'):
            print(f'[Guard] ⚠️ 警告: {guard_result["warning"]}')

    # ─── 检查是否有恢复策略（来自 Observe 的自适应恢复）───
    recovery_strategy = state.get('recovery_strategy')
    if recovery_strategy:
        logger._write('info', 'act', 'recovery_execution', {
            'action': recovery_strategy.get('action', ''),
            'adjustments': recovery_strategy.get('adjustments', {}),
        })
        print(f'[Act] 🔄 执行恢复策略: {recovery_strategy.get("action", "")}')
        return _execute_recovery(state, recovery_strategy)

    # ─── 选择执行计划模板 ───
    query = state.get('query', '')
    plan_name = select_plan(intent, query, version, issue_id)
    plan = PLAN_TEMPLATES.get(plan_name, PLAN_TEMPLATES['full_report'])

    logger._write('info', 'act', 'plan_selected', {
        'plan': plan_name, 'desc': plan['desc'],
        'need_full_pipeline': plan.get('need_full_pipeline', False),
    })
    print(f'[Act] 📋 计划: {plan_name} — {plan["desc"]}')

    # ─── 根据计划执行 ───
    tool_calls = []
    observations = []

    if plan.get('need_full_pipeline'):
        # 完整流水线（趋势 + TOP10 + 并行堆栈 + 历史判定 + TAPD）
        observations, tool_calls = _execute_full_report(
            project_id, version, start_date, end_date
        )
    else:
        # 按模板的 steps 逐步执行（不走完整流水线）
        for step in plan['steps']:
            tool_name = step['tool']
            alias = step['alias']
            args = _build_args(tool_name, project_id, version, start_date, end_date, issue_id)

            print(f'[Act] 调用 {tool_name}({json.dumps(args, ensure_ascii=False)[:80]})')
            result = execute_tool(tool_name, args)

            tool_calls.append({'tool': tool_name, 'alias': alias, 'args': args, 'success': result.get('success', False)})
            observations.append({
                'tool': tool_name, 'alias': alias,
                'success': result.get('success', False),
                'data': result.get('data') if result.get('success') else None,
                'error': result.get('error', ''),
            })

            if result.get('success'):
                print(f'[Act]   ✓ {tool_name} 成功')
            else:
                print(f'[Act]   ✗ {tool_name} 失败: {result.get("error", "")[:60]}')
            time.sleep(0.5)

    return {
        'tool_calls': tool_calls,
        'observations': observations,
        'step_count': step_count + 1,
    }


def _execute_full_report(project_id: str, version: str, start_date: str, end_date: str) -> tuple:
    """完整报告流程 — 与原 app.py generate_report() 功能对齐
    
    流程:
    1. 获取崩溃率趋势
    2. 获取 TOP10 问题
    3. 对每个问题: 拉完整堆栈
    4. 对每个问题: 用 LLM 判断是否为历史问题
    5. 对有 TAPD 关联的: 拉 TAPD 详情
    
    返回: (observations, tool_calls)
    """
    observations = []
    tool_calls = []
    emitter = get_emitter()

    logger = get_logger()
    t0 = time.time()

    # ── Step 1: 崩溃率趋势 ──
    if emitter:
        emitter.emit(EventType.TOOL_START, '📈 正在获取崩溃率趋势...', node='act')
    t1 = time.time()
    trend_result = execute_tool('get_crash_trend', {
        'project_id': project_id, 'version': version,
        'start_date': start_date, 'end_date': end_date,
    })
    trend_ms = int((time.time() - t1) * 1000)
    observations.append({
        'tool': 'get_crash_trend', 'alias': 'trend',
        'success': trend_result.get('success', False),
        'data': trend_result.get('data'),
        'error': trend_result.get('error', ''),
    })
    tool_calls.append({'tool': 'get_crash_trend', 'alias': 'trend', 'success': trend_result.get('success', False)})
    logger.log_tool_call('get_crash_trend', trend_result.get('success', False), trend_ms,
                         error=trend_result.get('error', ''))
    if emitter:
        if trend_result.get('success'):
            tr = trend_result.get('data', {})
            emitter.emit(EventType.TOOL_SUCCESS, f'✓ 崩溃率: {tr.get("minRate", "-")}% ~ {tr.get("maxRate", "-")}%', node='act')
        else:
            emitter.emit_tool_error('get_crash_trend', trend_result.get('error', ''))
    print(f'[Act]   {"✓" if trend_result.get("success") else "✗"} get_crash_trend ({trend_ms}ms)')
    time.sleep(1)

    # ── Step 2: TOP10 问题 ──
    if emitter:
        emitter.emit(EventType.TOOL_START, '📋 正在获取 TOP10 崩溃问题...', node='act')
    t2 = time.time()
    issues_result = execute_tool('get_top_issues', {
        'project_id': project_id, 'version': version,
        'start_date': start_date, 'end_date': end_date, 'top_n': 10,
    })
    issues_ms = int((time.time() - t2) * 1000)
    observations.append({
        'tool': 'get_top_issues', 'alias': 'top_issues',
        'success': issues_result.get('success', False),
        'data': issues_result.get('data'),
        'error': issues_result.get('error', ''),
    })
    tool_calls.append({'tool': 'get_top_issues', 'alias': 'top_issues', 'success': issues_result.get('success', False)})
    logger.log_tool_call('get_top_issues', issues_result.get('success', False), issues_ms,
                         error=issues_result.get('error', ''))
    if emitter:
        if issues_result.get('success'):
            count = len(issues_result.get('data', []))
            emitter.emit(EventType.TOOL_SUCCESS, f'✓ 获取到 {count} 个崩溃问题', node='act')
        else:
            emitter.emit_tool_error('get_top_issues', issues_result.get('error', ''))
    print(f'[Act]   {"✓" if issues_result.get("success") else "✗"} get_top_issues ({issues_ms}ms)')

    if not issues_result.get('success') or not issues_result.get('data'):
        return observations, tool_calls

    top_issues = issues_result['data']
    time.sleep(1)

    # ── Step 3 + 4: 并行处理（堆栈+历史判定+TAPD）──
    # 用 asyncio.gather + Semaphore(3) + 令牌桶(22次/分)
    # 替代原来的串行循环，耗时从 65s → 17s
    try:
        loop = asyncio.get_running_loop()
        # 已在异步环境中 → 不能直接 asyncio.run，用新线程
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(1) as pool:
            future = pool.submit(asyncio.run, _async_steps_3_4(top_issues, project_id, version))
            history_results, tapd_results = future.result()
    except RuntimeError:
        # 没有运行中的事件循环（CLI 等同步环境）
        history_results, tapd_results = asyncio.run(_async_steps_3_4(top_issues, project_id, version))

    # ── 把历史判定和 TAPD 结果合并回 top_issues ──
    for issue in top_issues:
        iid = issue.get('issueId', '')
        hist = history_results.get(iid, {})
        issue['isHistoryIssue'] = hist.get('isHistory', False)
        issue['historyDetail'] = hist

        tapd_detail = tapd_results.get(iid)
        if tapd_detail:
            issue['tapdDetail'] = tapd_detail

    # 更新 observations 里的 top_issues 数据（已包含历史+TAPD）
    for obs in observations:
        if obs['alias'] == 'top_issues':
            obs['data'] = top_issues

    # 额外记录历史判定摘要
    observations.append({
        'tool': 'check_history_issue', 'alias': 'history_summary',
        'success': True,
        'data': {
            'total': len(top_issues),
            'history_count': sum(1 for h in history_results.values() if h.get('isHistory')),
            'new_count': sum(1 for h in history_results.values() if not h.get('isHistory')),
            'details': history_results,
        },
        'error': '',
    })

    total_ms = int((time.time() - t0) * 1000)
    history_count = sum(1 for h in history_results.values() if h.get('isHistory'))
    logger._write('info', 'act', 'full_report_done', {
        'total_issues': len(top_issues),
        'history_count': history_count,
        'new_count': len(top_issues) - history_count,
        'tapd_count': len(tapd_results),
        'total_ms': total_ms,
    }, duration_ms=total_ms)

    print(f'[Act] 完整报告流程完成: {len(top_issues)} 条问题，'
          f'历史问题 {history_count} 条，'
          f'TAPD {len(tapd_results)} 条，耗时 {total_ms}ms')

    return observations, tool_calls


def _build_args(tool_name: str, project_id: str, version: str,
                start_date: str, end_date: str, issue_id: str) -> dict:
    """根据工具名构建参数"""
    if tool_name == 'get_crash_trend':
        return {
            'project_id': project_id,
            'version': version,
            'start_date': start_date,
            'end_date': end_date,
        }
    elif tool_name == 'get_top_issues':
        return {
            'project_id': project_id,
            'version': version,
            'start_date': start_date,
            'end_date': end_date,
            'top_n': 10,
        }
    elif tool_name == 'get_issue_full_stack':
        return {
            'project_id': project_id,
            'issue_id': issue_id,
        }
    elif tool_name == 'check_history_issue':
        return {
            'project_id': project_id,
            'issue_id': issue_id,
        }
    return {}


async def _async_steps_3_4(top_issues: list, project_id: str, version: str) -> tuple:
    """异步执行 Step 3(堆栈+历史判定) 和 Step 4(TAPD)
    
    并行策略:
    - Step 3: asyncio.gather 并发处理所有 issue（Semaphore 限制最多3并发）
    - Step 4: asyncio.gather 并发拉 TAPD（复用限流器）
    
    返回: (history_results, tapd_results)
    """
    # Step 3: 并行拉堆栈 + 判历史
    parallel_results = await parallel_process_issues(top_issues, project_id, version, max_concurrent=3)

    # 转换格式: {issue_id: history_data}
    history_results = {}
    for issue_id, result in parallel_results.items():
        if result.get('success'):
            history_results[issue_id] = result.get('history', {'isHistory': False})
        else:
            history_results[issue_id] = {'isHistory': False, 'reason': result.get('error', '处理失败')}

    # Step 4: 并行拉 TAPD
    tapd_results = await parallel_fetch_tapd(top_issues)

    return history_results, tapd_results


# ==================== 恢复策略执行 ====================

def _execute_recovery(state: dict, strategy: dict) -> dict:
    """
    执行 Observe 规划的恢复策略
    
    恢复策略类型：
    - use_key_stack_fallback: 用 top_issues 自带的 keyStack 替代完整堆栈做历史判定
    - skip_stack_and_history: 跳过堆栈和历史判定，直接输出已有数据
    - broaden_search_version: 扩大搜索版本范围重跑历史判定
    - trend_all_versions: 用全版本重拉趋势数据
    """
    adjustments = strategy.get('adjustments', {})
    observations = state.get('observations', [])
    project_id = state.get('project_id', '')
    version = state.get('version', '-1')
    start_date = state.get('start_date', '')
    end_date = state.get('end_date', '')
    step_count = state.get('step_count', 0)
    logger = get_logger()

    # ── 策略 A: 用 keyStack 兜底 ──
    if adjustments.get('use_key_stack_fallback'):
        # 从已有的 observations 中找 top_issues 数据
        top_obs = next((o for o in observations if o.get('alias') == 'top_issues' and o.get('success')), None)
        if not top_obs or not top_obs.get('data'):
            print('[Recovery] top_issues 数据不存在，无法用 keyStack 兜底')
            return {'step_count': step_count + 1, 'final_status': 'ok', 'recovery_strategy': None}

        top_issues = top_obs['data']
        recovered_count = 0

        for issue in top_issues:
            # 已经有堆栈的跳过
            if issue.get('historyDetail', {}).get('reason') != '堆栈为空':
                continue

            key_stack = issue.get('keyStack', '') or issue.get('crashDetail', '')
            if not key_stack:
                continue

            # 用 keyStack 做历史判定
            print(f'[Recovery] 用 keyStack 对 {issue.get("exceptionName","")[:25]} 做历史判定...')
            history_result = execute_tool('check_history_issue', {
                'project_id': project_id,
                'issue_id': issue.get('issueId', ''),
                'exp_stack': key_stack,
                'exp_exception': issue.get('exceptionName', ''),
            })

            if history_result.get('success') and history_result.get('data'):
                hist_data = history_result['data']
                issue['isHistoryIssue'] = hist_data.get('isHistory', False)
                issue['historyDetail'] = hist_data
                recovered_count += 1
                logger.log_history_check(
                    issue.get('issueId', ''), key_stack[:50],
                    'match' if hist_data.get('isHistory') else 'mismatch',
                    reason=f'keyStack兜底: {hist_data.get("reason", "")[:40]}',
                )

            time.sleep(1)

        print(f'[Recovery] keyStack 兜底完成: 恢复了 {recovered_count} 个 issue 的历史判定')
        logger._write('info', 'act', 'recovery_done', {
            'strategy': 'use_key_stack_fallback',
            'recovered_count': recovered_count,
        })

        # 注意: observations 是 Annotated[list, add]，只返回新增的记录
        return {
            'observations': [{
                'tool': '_recovery', 'alias': 'recovery_keystack',
                'success': True,
                'data': {'recovered_count': recovered_count, 'top_issues': top_issues},
                'error': '',
            }],
            'step_count': step_count + 1,
            'final_status': 'ok',
            'recovery_strategy': None,
        }

    # ── 策略 B: 跳过堆栈和历史判定 ──
    if adjustments.get('skip_stack_and_history'):
        print('[Recovery] 跳过堆栈和历史判定，使用已有数据生成报告')
        logger._write('info', 'act', 'recovery_done', {'strategy': 'skip_stack_and_history'})
        return {
            'step_count': step_count + 1,
            'final_status': 'ok',
            'recovery_strategy': None,
        }

    # ── 策略 C: 全版本重拉趋势 ──
    if adjustments.get('trend_all_versions'):
        print('[Recovery] 用全版本(-1)重拉崩溃率趋势...')
        trend_result = execute_tool('get_crash_trend', {
            'project_id': project_id, 'version': '-1',
            'start_date': start_date, 'end_date': end_date,
        })
        logger._write('info', 'act', 'recovery_done', {
            'strategy': 'trend_all_versions',
            'success': trend_result.get('success', False),
        })
        if trend_result.get('success'):
            print(f'[Recovery] 趋势数据恢复成功')

        # 只返回新增的趋势观测（会追加到 observations）
        return {
            'observations': [{
                'tool': 'get_crash_trend', 'alias': 'trend',
                'success': trend_result.get('success', False),
                'data': trend_result.get('data'),
                'error': trend_result.get('error', ''),
            }],
            'step_count': step_count + 1,
            'final_status': 'ok',
            'recovery_strategy': None,
        }

    # 未知策略，直接放行
    print(f'[Recovery] 未知恢复策略: {adjustments}，跳过')
    return {'step_count': step_count + 1, 'final_status': 'ok', 'recovery_strategy': None}
