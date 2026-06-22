"""报告生成工具 — 用 LLM 生成结构化崩溃分析报告"""
import json
from ..config import PROJECTS
from ..llm_client import call_llm


def execute(project_id: str, version: str, start_date: str, end_date: str,
            trend_data: dict = None, top_issues: list = None) -> str:
    """根据收集到的数据生成 Markdown 格式崩溃分析报告"""
    project = PROJECTS.get(project_id, {})
    project_name = project.get('name', project_id)

    # 格式化日期显示
    start_fmt = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:8]}" if len(start_date) == 8 else start_date
    end_fmt = f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:8]}" if len(end_date) == 8 else end_date

    # 构建 LLM prompt
    prompt = f"""请根据以下崩溃数据生成一份简洁的崩溃分析报告（Markdown格式）。

## 基本信息
- 项目: {project_name}
- 版本: {version}
- 时间范围: {start_fmt} ~ {end_fmt}

## 崩溃率趋势数据
{json.dumps(trend_data, ensure_ascii=False, indent=2) if trend_data else '暂无数据'}

## TOP 崩溃问题
{json.dumps(top_issues, ensure_ascii=False, indent=2) if top_issues else '暂无数据'}

## 报告格式要求
1. 标题：项目名 + 时间范围 + "崩溃分析报告"
2. 概览：一段话说明崩溃率情况和风险等级（<0.5%低风险，0.5-2%中风险，>2%高风险）
3. TOP问题表格：序号 | 异常名 | 崩溃次数 | 影响用户 | 占比 | 是否历史问题
4. 重点问题分析：对 Top3 问题简要分析（基于异常名和堆栈推断可能原因）
5. 建议：优先处理哪些问题

请直接输出 Markdown，不要包含```markdown```标记。"""

    report = call_llm(prompt)
    if not report:
        # LLM 不可用时用模板兜底
        report = _fallback_report(project_name, version, start_fmt, end_fmt, trend_data, top_issues)

    return report


def _fallback_report(project_name, version, start_fmt, end_fmt, trend_data, top_issues) -> str:
    """LLM 不可用时的模板报告"""
    lines = [
        f"# {project_name} 崩溃分析报告",
        f"**时间范围:** {start_fmt} ~ {end_fmt}  |  **版本:** {version}",
        "",
    ]

    if trend_data:
        min_r = trend_data.get('minRate', '-')
        max_r = trend_data.get('maxRate', '-')
        total_access = trend_data.get('totalAccess', 0)
        total_crash_user = trend_data.get('totalCrashUser', 0)
        lines.append(f"## 概览")
        lines.append(f"- 崩溃率范围: {min_r}% ~ {max_r}%")
        lines.append(f"- 联网设备: {total_access:,}")
        lines.append(f"- 影响设备: {total_crash_user:,}")
        lines.append("")

    if top_issues:
        lines.append("## TOP 崩溃问题")
        lines.append("| # | 异常名 | 崩溃次数 | 影响用户 | 占比 |")
        lines.append("|---|--------|----------|----------|------|")
        for i, issue in enumerate(top_issues[:10]):
            lines.append(
                f"| {i+1} | {issue.get('exceptionName', '-')[:40]} | "
                f"{issue.get('crashCount', 0)} | {issue.get('affectedUsers', 0)} | "
                f"{issue.get('crashRatio', 0)}% |"
            )
        lines.append("")

    return '\n'.join(lines)
