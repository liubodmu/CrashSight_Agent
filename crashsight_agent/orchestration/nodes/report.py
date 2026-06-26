"""Report 节点 — 生成最终输出（纯数据展示，不依赖 LLM）"""
import json
from ...config import PROJECTS


def report_node(state: dict) -> dict:
    """
    根据意图和工具结果，生成最终回答:
    - crash_report → 核心指标 + TOP10 表格（纯数据，不调 LLM）
    - trend_query → 简短趋势描述
    - issue_detail → 堆栈+设备信息
    - history_check → 是否历史问题
    - error → 错误说明
    """
    intent = state.get('intent', '')
    observations = state.get('observations', [])
    project_id = state.get('project_id', '')
    version = state.get('version', '')
    start_date = state.get('start_date', '')
    end_date = state.get('end_date', '')
    last_error = state.get('last_error', '')
    final_status = state.get('final_status', 'ok')

    project = PROJECTS.get(project_id, {})
    project_name = project.get('name', project_id)

    # 错误情况
    if final_status == 'error':
        answer = f"抱歉，查询失败: {last_error}\n\n可能原因:\n• CrashSight API 超时/限流\n• 网络连接不稳定\n\n请稍后重试。"
        return {'answer': answer, 'final_status': 'error'}

    # 收集成功的结果（后面的同名 alias 覆盖前面的，recovery 结果优先）
    results = {}
    for obs in observations:
        if obs.get('success') and obs.get('data'):
            alias = obs['alias']
            results[alias] = obs['data']
            # recovery_keystack 包含更新后的 top_issues
            if alias == 'recovery_keystack' and isinstance(obs['data'], dict):
                recovered_issues = obs['data'].get('top_issues')
                if recovered_issues:
                    results['top_issues'] = recovered_issues

    # 根据意图生成回答（支持多意图合并输出）
    intents = state.get('intents', [])
    deferred_intents = state.get('deferred_intents', [])

    if intents and len(intents) > 1:
        # 多意图：每个意图生成一段，合并
        answer_parts = []
        for item in intents:
            i = item['intent']
            part = _generate_for_intent(i, project_name, version, start_date, end_date, results, last_error)
            if part:
                answer_parts.append(part)
        answer = '\n\n---\n\n'.join(answer_parts)
    else:
        # 单意图
        answer = _generate_for_intent(intent, project_name, version, start_date, end_date, results, last_error)

    # 如果有被推迟的意图，在末尾提示
    if deferred_intents:
        deferred_names = {
            'crash_report': '崩溃报告', 'trend_query': '趋势查询',
            'history_check': '历史问题判定', 'issue_detail': '堆栈详情',
            'compare': '数据对比',
        }
        deferred_list = [deferred_names.get(d['intent'], d['intent']) for d in deferred_intents]
        answer += f'\n\n---\n\n💡 您还提到了以下需求，可以在下一轮继续提问：\n'
        for name in deferred_list:
            answer += f'• {name}\n'

    return {
        'answer': answer,
        'report_markdown': answer if intent == 'crash_report' else None,
        'final_status': 'ok',
    }


def _generate_for_intent(intent, project_name, version, start_date, end_date, results, last_error) -> str:
    """根据单个意图生成对应的回答片段"""
    if intent == 'crash_report':
        return _generate_report(project_name, version, start_date, end_date, results)
    elif intent == 'trend_query':
        return _generate_trend_answer(project_name, version, start_date, end_date, results)
    elif intent == 'issue_detail':
        return _generate_detail_answer(results)
    elif intent == 'history_check':
        return _generate_history_answer(results, last_error)
    else:
        return _generate_generic_answer(intent, results)


def _generate_report(project_name, version, start_date, end_date, results) -> str:
    """纯数据报告：核心指标卡片 + 崩溃趋势 + TOP10 问题详情（不调 LLM）"""
    trend = results.get('trend', {})
    top_issues = results.get('top_issues', [])

    # 格式化日期
    s = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:8]}" if len(start_date) == 8 else start_date
    e = f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:8]}" if len(end_date) == 8 else end_date

    lines = []

    # ── 标题 ──
    lines.append(f'# {project_name} 崩溃分析报告')
    lines.append(f'**版本:** {version}  |  **时间:** {s} ~ {e}')
    lines.append('')

    # ── 核心指标卡片 ──
    if trend:
        min_rate = trend.get('minRate', '0')
        max_rate = trend.get('maxRate', '0')
        min_access = trend.get('minAccess', 0)
        max_access = trend.get('maxAccess', 0)
        min_crash_user = trend.get('minCrashUser', 0)
        max_crash_user = trend.get('maxCrashUser', 0)

        lines.append('## 核心指标')
        lines.append('')
        lines.append(f'| 📉 崩溃率范围 | 📱 联网设备总数 | ⚠️ 影响设备数 |')
        lines.append(f'|:---:|:---:|:---:|')
        lines.append(f'| **{min_rate}% - {max_rate}%** | **{_fmt_num(min_access)} - {_fmt_num(max_access)}** | **{_fmt_num(min_crash_user)} - {_fmt_num(max_crash_user)}** |')
        lines.append('')

    # ── 崩溃趋势（逐日/逐时数据） ──
    trend_points = trend.get('trendPoints', []) if trend else []
    if trend_points:
        granularity = trend.get('granularity', 'day')
        date_label = '时间' if granularity == 'hour' else '日期'
        lines.append('## 📈 崩溃趋势')
        lines.append('')
        lines.append(f'| {date_label} | 设备崩溃率(%) | 联网设备数 |')
        lines.append('|------|:---:|:---:|')
        for point in trend_points:
            date = point.get('date', '-')
            rate = point.get('crashRate', '-')
            access = point.get('accessUser', '-')
            lines.append(f'| {date} | {rate} | {_fmt_num(access)} |')
        lines.append('')

    # ── TOP 10 崩溃问题详情 ──
    if top_issues:
        lines.append('## 🔥 TOP 10 崩溃问题详情')
        lines.append('')

        for i, issue in enumerate(top_issues[:10]):
            exc_name = issue.get('exceptionName', '-')
            crash_count = issue.get('crashCount', 0)
            affected_users = issue.get('affectedUsers', 0)
            crash_ratio = issue.get('crashRatio', '-')
            first_version = issue.get('firstCrashVersion', '-')
            first_time = issue.get('firstUploadTime', '-')
            key_stack = issue.get('keyStack', '')
            is_history = issue.get('isHistoryIssue', False)
            tapd_detail = issue.get('tapdDetail', {})
            tag_list = issue.get('tagInfoList', [])

            # 状态标签
            status_tag = '🟡 新问题' if not is_history else '🔴 历史问题'

            lines.append(f'### #{i+1} {exc_name}')
            lines.append(f'**状态:** {status_tag}  |  **崩溃次数:** {_fmt_num(crash_count)}  |  **影响用户:** {_fmt_num(affected_users)}  |  **占比:** {crash_ratio}%')
            lines.append('')
            lines.append(f'- 首发版本: {first_version}')
            lines.append(f'- 首次上报: {first_time}')

            # 堆栈
            if key_stack:
                stack_short = key_stack[:200]
                lines.append(f'- 堆栈:')
                lines.append(f'```')
                lines.append(stack_short)
                lines.append(f'```')

            # TAPD 特征
            if tapd_detail:
                tapd_status = tapd_detail.get('status', '-')
                tapd_flow = tapd_detail.get('flow', '')
                tapd_participator = tapd_detail.get('participator', '')
                tapd_resolve = tapd_detail.get('lastResolveTime', '')
                tapd_reject_time = tapd_detail.get('rejectTime', '')
                tapd_url = tapd_detail.get('url', '')
                tapd_version = tapd_detail.get('version', '')
                tapd_static_days = tapd_detail.get('staticDays', '')

                tapd_info = f'处理状态：{tapd_status}'
                if tapd_flow:
                    tapd_info += f'；流转路径：{tapd_flow}'
                if tapd_participator:
                    tapd_info += f'；参与人：{tapd_participator}'
                if tapd_resolve:
                    tapd_info += f'；最后解决/拒绝时间：{tapd_resolve}'
                if tapd_static_days:
                    tapd_info += f'，超过静默期：{tapd_static_days}'
                if tapd_version:
                    tapd_info += f' 有新提单 版本: {tapd_version}'
                if tapd_url:
                    tapd_info += f' 链接: {tapd_url}'

                lines.append(f'- 【TAPD特征】{tapd_info}')

            # 堆栈特征
            if key_stack:
                lines.append(f'- 【堆栈特征】{exc_name}；关键帧：{key_stack[:80]}')

            # 标签特征
            if tag_list:
                tag_names = []
                for t in (tag_list if isinstance(tag_list, list) else []):
                    if isinstance(t, dict):
                        tag_names.append(t.get('tagName') or t.get('name') or str(t.get('tagId', '')))
                    elif isinstance(t, str):
                        tag_names.append(t)
                if tag_names:
                    lines.append(f'- 【标签特征】业务标签：{"；".join(tag_names[:5])}')

            # 历史问题详情
            hist = issue.get('historyDetail', {})
            if is_history and hist:
                prod_issue = hist.get('prodIssueId', '')
                prod_url = hist.get('prodUrl', '')
                lines.append(f'- 【正式服关联】Issue: `{prod_issue}`' + (f' [查看]({prod_url})' if prod_url else ''))

            lines.append('')

    else:
        lines.append('## TOP 10 崩溃问题')
        lines.append('')
        lines.append('本周期内无崩溃问题数据。')

    return '\n'.join(lines)


def _generate_trend_answer(project_name, version, start_date, end_date, results) -> str:
    trend = results.get('trend', {})
    if not trend:
        return "未获取到趋势数据。"

    s = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:8]}" if len(start_date) == 8 else start_date
    e = f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:8]}" if len(end_date) == 8 else end_date

    min_r = trend.get('minRate', '-')
    max_r = trend.get('maxRate', '-')
    avg_r = trend.get('avgRate', '-')
    min_access = trend.get('minAccess', 0)
    max_access = trend.get('maxAccess', 0)
    min_crash = trend.get('minCrashUser', 0)
    max_crash = trend.get('maxCrashUser', 0)

    lines = [
        f'## {project_name} 崩溃率趋势',
        f'**版本:** {version}  |  **时间:** {s} ~ {e}',
        '',
        f'| 📉 崩溃率范围 | 📱 联网设备总数 | ⚠️ 影响设备数 |',
        f'|:---:|:---:|:---:|',
        f'| **{min_r}% - {max_r}%** | **{_fmt_num(min_access)} - {_fmt_num(max_access)}** | **{_fmt_num(min_crash)} - {_fmt_num(max_crash)}** |',
    ]

    daily_data = trend.get('dailyData', [])
    if daily_data:
        lines.append('')
        lines.append('| 日期 | 崩溃率(%) | 联网设备 | 崩溃设备 |')
        lines.append('|------|:---:|:---:|:---:|')
        for day in daily_data:
            lines.append(f'| {day.get("date","-")} | {day.get("crashRate","-")} | {_fmt_num(day.get("accessCount","-"))} | {_fmt_num(day.get("crashUser","-"))} |')

    return '\n'.join(lines)


def _generate_detail_answer(results) -> str:
    stack_data = results.get('stack', {})
    if not stack_data or not stack_data.get('callStack'):
        return "未获取到堆栈信息（可能崩溃记录已过期）。"

    stack_lines = stack_data['callStack'].split('\n')[:20]
    device = f"{stack_data.get('brand', '')} {stack_data.get('model', '')} (OS: {stack_data.get('osVersion', '')})"

    return (
        f"**设备:** {device}\n"
        f"**线程:** {stack_data.get('threadName', '-')}\n\n"
        f"**堆栈 (前20行):**\n```\n" + '\n'.join(stack_lines) + "\n```"
    )


def _generate_history_answer(results, last_error) -> str:
    history = results.get('history', {})
    if not history:
        if last_error:
            return f"无法判断历史问题: {last_error}"
        return "未搜索到正式服匹配记录，**判定为新问题**。"

    if history.get('isHistory'):
        return (
            f"✅ **是历史问题**\n\n"
            f"• 正式服 Issue: `{history.get('prodIssueId', '')}`\n"
            f"• 异常名: {history.get('prodException', '')}\n"
            f"• 正式服崩溃次数: {history.get('prodCrashCount', 0)}\n"
            f"• 影响用户: {history.get('prodAffectedUsers', 0)}\n"
            f"• [查看正式服详情]({history.get('prodUrl', '')})"
        )
    else:
        return f"❌ **不是历史问题**（正式服未找到匹配）\n\n原因: {history.get('reason', '候选堆栈不匹配')}"


def _generate_generic_answer(intent, results) -> str:
    if results:
        return f"查询完成，获取到 {len(results)} 组数据。\n\n{json.dumps(results, ensure_ascii=False, indent=2)[:1000]}"
    return "查询完成，但未获取到有效数据。"


def _fmt_num(n) -> str:
    """格式化数字：加千分位逗号"""
    if isinstance(n, (int, float)):
        return f'{int(n):,}'
    return str(n)
