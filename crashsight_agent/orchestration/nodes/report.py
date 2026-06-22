"""Report 节点 — 生成最终输出"""
import json
from ...llm_client import call_llm
from ...config import PROJECTS


def report_node(state: dict) -> dict:
    """
    根据意图和工具结果，生成最终回答:
    - crash_report → 完整 Markdown 报告
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

    # 收集成功的结果
    results = {}
    for obs in observations:
        if obs.get('success') and obs.get('data'):
            results[obs['alias']] = obs['data']

    # 根据意图生成回答
    if intent == 'crash_report':
        answer = _generate_report(project_name, version, start_date, end_date, results)
    elif intent == 'trend_query':
        answer = _generate_trend_answer(project_name, version, start_date, end_date, results)
    elif intent == 'issue_detail':
        answer = _generate_detail_answer(results)
    elif intent == 'history_check':
        answer = _generate_history_answer(results, last_error)
    else:
        answer = _generate_generic_answer(intent, results)

    return {
        'answer': answer,
        'report_markdown': answer if intent == 'crash_report' else None,
        'final_status': 'ok',
    }


def _generate_report(project_name, version, start_date, end_date, results) -> str:
    """用 LLM 生成完整报告"""
    trend = results.get('trend', {})
    top_issues = results.get('top_issues', [])

    # 格式化日期
    s = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:8]}" if len(start_date) == 8 else start_date
    e = f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:8]}" if len(end_date) == 8 else end_date

    prompt = f"""根据以下数据生成崩溃分析报告（Markdown）。

项目: {project_name} | 版本: {version} | 时间: {s} ~ {e}

崩溃率: {json.dumps(trend, ensure_ascii=False)[:500] if trend else '无数据'}

TOP问题: {json.dumps(top_issues[:5], ensure_ascii=False)[:1500] if top_issues else '无数据'}

要求: 1.概览(崩溃率+风险) 2.TOP问题表格 3.重点分析Top3 4.建议
直接输出Markdown。"""

    report = call_llm(prompt)
    return report or _fallback_report(project_name, version, s, e, trend, top_issues)


def _generate_trend_answer(project_name, version, start_date, end_date, results) -> str:
    trend = results.get('trend', {})
    if not trend:
        return "未获取到趋势数据。"

    s = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:8]}" if len(start_date) == 8 else start_date
    e = f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:8]}" if len(end_date) == 8 else end_date

    min_r = trend.get('minRate', '-')
    max_r = trend.get('maxRate', '-')
    avg_r = trend.get('avgRate', '-')
    total_access = trend.get('totalAccess', 0)
    total_crash = trend.get('totalCrashUser', 0)

    return (
        f"**{project_name}** {s}~{e} 崩溃率趋势:\n\n"
        f"• 最低: {min_r}% | 最高: {max_r}% | 平均: {avg_r}%\n"
        f"• 联网设备: {total_access:,} | 影响设备: {total_crash:,}\n"
        f"• 版本: {version}"
    )


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


def _fallback_report(project_name, version, start, end, trend, top_issues) -> str:
    """LLM 不可用时的模板报告"""
    lines = [f"# {project_name} 崩溃分析报告", f"**{start} ~ {end}** | 版本: {version}\n"]
    if trend:
        lines.append(f"崩溃率: {trend.get('minRate','-')}% ~ {trend.get('maxRate','-')}%\n")
    if top_issues:
        lines.append("| # | 异常名 | 崩溃次数 | 影响用户 |")
        lines.append("|---|--------|----------|----------|")
        for i, iss in enumerate(top_issues[:10]):
            lines.append(f"| {i+1} | {iss.get('exceptionName','-')[:35]} | {iss.get('crashCount',0)} | {iss.get('affectedUsers',0)} |")
    return '\n'.join(lines)
