"""并行执行器 — 用 asyncio 并发处理多个 issue（堆栈+历史判定）

替代 _execute_full_report 中的串行循环:
  原来: for issue in issues: get_stack → check_history → sleep  (65s)
  现在: asyncio.gather + semaphore + 令牌桶                     (17s)
"""
import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from . import execute_tool
from .rate_limiter import api_limiter, api_semaphore
from ..streaming.events import get_emitter, EventType


# 线程池（同步工具在线程里跑，不阻塞事件循环）
_thread_pool = ThreadPoolExecutor(max_workers=5)


async def parallel_process_issues(
    issues: list,
    project_id: str,
    version: str,
    max_concurrent: int = 3,
) -> dict:
    """并行处理多个 issue：拉堆栈 + 判断历史问题
    
    参数:
        issues: TOP 问题列表
        project_id: 项目 ID
        version: 版本号
        max_concurrent: 最大并发数
        
    返回:
        {issue_id: {'stack': ..., 'history': ..., 'success': True/False}}
    """
    emitter = get_emitter()
    semaphore = asyncio.Semaphore(max_concurrent)
    results = {}
    total = len(issues)

    if emitter:
        emitter.emit(EventType.TOOL_START, f'🔍 并行处理 {total} 个问题（最大并发 {max_concurrent}）...', node='act')

    start_time = time.time()

    # 创建所有任务
    tasks = [
        _process_single_issue(issue, project_id, version, semaphore, i, total)
        for i, issue in enumerate(issues)
    ]

    # 并行执行
    task_results = await asyncio.gather(*tasks, return_exceptions=True)

    # 收集结果
    for issue, result in zip(issues, task_results):
        issue_id = issue.get('issueId', '')
        if isinstance(result, Exception):
            results[issue_id] = {'success': False, 'error': str(result), 'history': {'isHistory': False, 'reason': '处理异常'}}
        else:
            results[issue_id] = result

    elapsed = time.time() - start_time
    success_count = sum(1 for r in results.values() if r.get('success'))

    if emitter:
        emitter.emit(EventType.TOOL_SUCCESS,
                     f'✓ 并行处理完成: {success_count}/{total} 成功，耗时 {elapsed:.1f}s',
                     node='act')

    print(f'[Parallel] 完成: {success_count}/{total} 成功，耗时 {elapsed:.1f}s (并发={max_concurrent})')
    return results


async def _process_single_issue(
    issue: dict,
    project_id: str,
    version: str,
    semaphore: asyncio.Semaphore,
    index: int,
    total: int,
) -> dict:
    """处理单个 issue：拉堆栈 + 判历史"""
    issue_id = issue.get('issueId', '')
    exc_name = issue.get('exceptionName', '')
    emitter = get_emitter()

    async with semaphore:  # 并发控制
        # ── 拉堆栈 ──
        await api_limiter.acquire()  # 限流
        if emitter:
            emitter.emit(EventType.TOOL_START,
                         f'  [{index+1}/{total}] {exc_name[:25]} — 获取堆栈',
                         node='act')

        loop = asyncio.get_event_loop()
        stack_result = await loop.run_in_executor(_thread_pool, execute_tool, 'get_issue_full_stack', {
            'project_id': project_id,
            'issue_id': issue_id,
            'version': version,
        })

        call_stack = ''
        if stack_result.get('success') and stack_result.get('data'):
            call_stack = stack_result['data'].get('callStackFull', '') or stack_result['data'].get('callStack', '')

        if not call_stack:
            if emitter:
                emitter.emit(EventType.WARNING,
                             f'  [{index+1}/{total}] {exc_name[:25]} — 堆栈为空，跳过',
                             node='act')
            return {'success': True, 'stack': '', 'history': {'isHistory': False, 'reason': '堆栈为空'}}

        # ── 判断历史问题 ──
        await api_limiter.acquire()  # 限流
        if emitter:
            emitter.emit(EventType.HISTORY_COMPARE,
                         f'  [{index+1}/{total}] {exc_name[:25]} — 🤖 LLM 判断历史问题',
                         node='act')

        history_result = await loop.run_in_executor(_thread_pool, execute_tool, 'check_history_issue', {
            'project_id': project_id,
            'issue_id': issue_id,
            'exp_stack': call_stack,
            'exp_exception': exc_name,
        })

        history_data = history_result.get('data', {}) if history_result.get('success') else {'isHistory': False, 'reason': '判定失败'}

        # 发射结果事件
        if emitter:
            is_hist = history_data.get('isHistory', False)
            label = '✅ 历史问题' if is_hist else '❌ 新问题'
            emitter.emit(EventType.HISTORY_RESULT,
                         f'  [{index+1}/{total}] {exc_name[:25]} — {label}',
                         node='act')

        return {'success': True, 'stack': call_stack, 'history': history_data}


async def parallel_fetch_tapd(issues: list) -> dict:
    """并行拉取 TAPD 详情"""
    emitter = get_emitter()
    results = {}
    
    tapd_issues = [iss for iss in issues if iss.get('tapdBug') and iss['tapdBug'].get('workspaceId')]
    if not tapd_issues:
        return results

    if emitter:
        emitter.emit(EventType.TOOL_START, f'🎫 并行获取 {len(tapd_issues)} 条 TAPD 详情...', node='act')

    async def _fetch_one_tapd(issue):
        tapd = issue['tapdBug']
        await api_limiter.acquire()
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(_thread_pool, execute_tool, 'get_tapd_bug_detail', {
            'workspace_id': tapd['workspaceId'],
            'bug_id': tapd['id'],
        })
        if result.get('success'):
            return issue['issueId'], result['data']
        return issue['issueId'], None

    tasks = [_fetch_one_tapd(iss) for iss in tapd_issues]
    task_results = await asyncio.gather(*tasks, return_exceptions=True)

    for r in task_results:
        if isinstance(r, tuple):
            issue_id, data = r
            if data:
                results[issue_id] = data

    if emitter:
        emitter.emit(EventType.TOOL_SUCCESS, f'✓ TAPD 获取完成 ({len(results)} 条)', node='act')

    return results
