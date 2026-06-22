"""Act 节点 — 根据意图调用工具（带安全守卫 + 流式事件 + 并行执行）"""
import json
import time
import asyncio
from ...tools import execute_tool
from ...tools.guard import check_query_safety
from ...tools.parallel_executor import parallel_process_issues, parallel_fetch_tapd
from ...config import PROJECTS
from ...streaming.events import get_emitter, EventType


# 各意图对应的工具调用计划
INTENT_PLANS = {
    'crash_report': [
        {'tool': 'get_crash_trend', 'alias': 'trend'},
        {'tool': 'get_top_issues', 'alias': 'top_issues'},
    ],
    'trend_query': [
        {'tool': 'get_crash_trend', 'alias': 'trend'},
    ],
    'issue_detail': [
        {'tool': 'get_issue_full_stack', 'alias': 'stack'},
    ],
    'history_check': [
        {'tool': 'get_issue_full_stack', 'alias': 'stack'},
        # check_history_issue 在 observe 阶段根据 stack 结果决定是否调用
    ],
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

    # ─── 根据意图执行完整流程 ───
    tool_calls = []
    observations = []

    if intent == 'crash_report':
        # 完整报告流程（与原前后端程序一致）:
        # Step 1: 崩溃率趋势
        # Step 2: TOP10 问题列表
        # Step 3: 逐条拉堆栈 + 判断历史问题
        # Step 4: 拉 TAPD 详情
        observations, tool_calls = _execute_full_report(
            project_id, version, start_date, end_date
        )
    else:
        # 其他意图：按计划执行
        plan = INTENT_PLANS.get(intent, [])
        for step in plan:
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

    # ── Step 1: 崩溃率趋势 ──
    if emitter:
        emitter.emit(EventType.TOOL_START, '📈 正在获取崩溃率趋势...', node='act')
    trend_result = execute_tool('get_crash_trend', {
        'project_id': project_id, 'version': version,
        'start_date': start_date, 'end_date': end_date,
    })
    observations.append({
        'tool': 'get_crash_trend', 'alias': 'trend',
        'success': trend_result.get('success', False),
        'data': trend_result.get('data'),
        'error': trend_result.get('error', ''),
    })
    tool_calls.append({'tool': 'get_crash_trend', 'alias': 'trend', 'success': trend_result.get('success', False)})
    if emitter:
        if trend_result.get('success'):
            tr = trend_result.get('data', {})
            emitter.emit(EventType.TOOL_SUCCESS, f'✓ 崩溃率: {tr.get("minRate", "-")}% ~ {tr.get("maxRate", "-")}%', node='act')
        else:
            emitter.emit_tool_error('get_crash_trend', trend_result.get('error', ''))
    print(f'[Act]   {"✓" if trend_result.get("success") else "✗"} get_crash_trend')
    time.sleep(1)

    # ── Step 2: TOP10 问题 ──
    if emitter:
        emitter.emit(EventType.TOOL_START, '📋 正在获取 TOP10 崩溃问题...', node='act')
    issues_result = execute_tool('get_top_issues', {
        'project_id': project_id, 'version': version,
        'start_date': start_date, 'end_date': end_date, 'top_n': 10,
    })
    observations.append({
        'tool': 'get_top_issues', 'alias': 'top_issues',
        'success': issues_result.get('success', False),
        'data': issues_result.get('data'),
        'error': issues_result.get('error', ''),
    })
    tool_calls.append({'tool': 'get_top_issues', 'alias': 'top_issues', 'success': issues_result.get('success', False)})
    if emitter:
        if issues_result.get('success'):
            count = len(issues_result.get('data', []))
            emitter.emit(EventType.TOOL_SUCCESS, f'✓ 获取到 {count} 个崩溃问题', node='act')
        else:
            emitter.emit_tool_error('get_top_issues', issues_result.get('error', ''))
    print(f'[Act]   {"✓" if issues_result.get("success") else "✗"} get_top_issues')

    if not issues_result.get('success') or not issues_result.get('data'):
        return observations, tool_calls

    top_issues = issues_result['data']
    time.sleep(1)

    # ── Step 3 + 4: 并行处理（堆栈+历史判定+TAPD）──
    # 用 asyncio.gather + Semaphore(3) + 令牌桶(22次/分)
    # 替代原来的串行循环，耗时从 65s → 17s
    loop = asyncio.get_event_loop()
    if loop.is_running():
        # 已在异步环境中（FastAPI），创建新线程跑
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(1) as pool:
            future = pool.submit(asyncio.run, _async_steps_3_4(top_issues, project_id, version))
            history_results, tapd_results = future.result()
    else:
        # CLI 等同步环境
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

    print(f'[Act] 完整报告流程完成: {len(top_issues)} 条问题，'
          f'历史问题 {sum(1 for h in history_results.values() if h.get("isHistory"))} 条，'
          f'TAPD {len(tapd_results)} 条')

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
            'version': version,
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
