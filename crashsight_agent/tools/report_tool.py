"""报告生成工具 — 原版 generate_summary + analyze_crash 规则模板，不用 LLM"""
import re
from ..config import PROJECTS


def execute(project_id: str, version: str, start_date: str, end_date: str,
            trend_data: dict = None, top_issues: list = None) -> str:
    """生成完整崩溃分析报告（格式与原 app.py 一致）"""
    project = PROJECTS.get(project_id, {})
    project_name = project.get('name', project_id)

    # 格式化日期
    s = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:8]}" if len(start_date) == 8 else start_date
    e = f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:8]}" if len(end_date) == 8 else end_date

    report_lines = []
    report_lines.append(f'# {project_name} 崩溃分析报告')
    report_lines.append(f'**版本:** {version}  |  **时间:** {s} ~ {e}')
    report_lines.append('')

    # ── 崩溃率概况 ──
    report_lines.append(generate_summary(trend_data, top_issues))
    report_lines.append('')

    # ── TOP 问题表格 ──
    if top_issues:
        report_lines.append('## TOP 崩溃问题')
        report_lines.append('')
        report_lines.append('| # | 异常名 | 崩溃次数 | 影响用户 | 占比 | 历史问题 | TAPD |')
        report_lines.append('|---|--------|----------|----------|------|----------|------|')
        for i, issue in enumerate(top_issues[:10]):
            exc = issue.get('exceptionName', '-')[:35]
            crash = issue.get('crashCount', 0)
            users = issue.get('affectedUsers', 0)
            ratio = f"{issue.get('crashRatio', 0)}%"
            is_hist = '✅是' if issue.get('isHistoryIssue') else '❌否'
            tapd = issue.get('tapdDetail', {}).get('status', '') or (issue.get('tapdBug', {}) or {}).get('title', '')[:15] or '-'
            report_lines.append(f'| {i+1} | {exc} | {crash} | {users} | {ratio} | {is_hist} | {tapd} |')
        report_lines.append('')

        # ── 逐条分析 ──
        report_lines.append('## 问题详情')
        report_lines.append('')
        for i, issue in enumerate(top_issues[:10]):
            report_lines.append(f'### #{i+1} {issue.get("exceptionName", "-")}')
            report_lines.append(f'- Issue ID: `{issue.get("issueId", "-")}`')
            report_lines.append(f'- 崩溃次数: {issue.get("crashCount", 0)} | 影响用户: {issue.get("affectedUsers", 0)}')
            report_lines.append(f'- 首发版本: {issue.get("firstCrashVersion", "-")} | 首次上报: {issue.get("firstUploadTime", "-")}')

            # 历史问题
            hist = issue.get('historyDetail', {})
            if issue.get('isHistoryIssue') and hist:
                report_lines.append(f'- **历史问题** ✅ 正式服 Issue: `{hist.get("prodIssueId", "")}`')
                if hist.get('prodUrl'):
                    report_lines.append(f'  - [正式服详情]({hist["prodUrl"]})')
            else:
                report_lines.append(f'- **新问题** ❌')

            # TAPD
            tapd_detail = issue.get('tapdDetail')
            if tapd_detail:
                report_lines.append(f'- TAPD: {tapd_detail.get("title", "-")} | 状态: {tapd_detail.get("status", "-")}')
                if tapd_detail.get('url'):
                    report_lines.append(f'  - [TAPD链接]({tapd_detail["url"]})')

            # analyze_crash 分析文本
            analysis = analyze_crash(
                issue.get('exceptionName', ''),
                '',
                issue.get('keyStack', ''),
                (issue.get('tapdBug') or {}).get('title', ''),
                '',
                tapd_detail=tapd_detail,
                tag_list=issue.get('tagInfoList'),
                thread_name=issue.get('threadName', ''),
            )
            if analysis:
                report_lines.append(f'- 分析: {analysis}')

            report_lines.append('')

    return '\n'.join(report_lines)


def generate_summary(trend_data: dict, top_issues: list) -> str:
    """生成综合分析文本（与原 app.py generate_summary 一致）"""
    lines = []

    # 崩溃率概况
    cr = trend_data or {}
    min_rate = cr.get('minRate', '-')
    max_rate = cr.get('maxRate', '-')
    total_access = cr.get('totalAccess', 0)
    total_crash_user = cr.get('totalCrashUser', 0)
    min_access = cr.get('minAccess', '-')
    max_access = cr.get('maxAccess', '-')
    min_crash_user = cr.get('minCrashUser', '-')
    max_crash_user = cr.get('maxCrashUser', '-')

    if min_rate not in ('-', 'N/A') and max_rate not in ('-', 'N/A'):
        lines.append(f'【崩溃率概况】本统计周期内崩溃率范围 {min_rate}%～{max_rate}%，联网设备 {min_access}～{max_access}，影响设备 {min_crash_user}～{max_crash_user}。')
    else:
        lines.append(f'【崩溃率概况】联网设备 {total_access}，影响设备 {total_crash_user}。')

    if not top_issues:
        lines.append('本周期内无崩溃问题。')
        return '\n'.join(lines)

    # 统计历史/新问题
    history_count = sum(1 for i in top_issues if i.get('isHistoryIssue'))
    new_count = len(top_issues) - history_count
    lines.append(f'【问题构成】TOP{len(top_issues)} 中历史问题 {history_count} 个，新问题 {new_count} 个。')

    # 风险评估
    try:
        max_r = float(str(max_rate).replace('%', ''))
        if max_r < 0.5:
            risk = '低风险'
        elif max_r < 2:
            risk = '中风险'
        else:
            risk = '高风险'
        lines.append(f'【风险评估】{risk}（最高崩溃率 {max_rate}%）')
    except:
        pass

    return '\n'.join(lines)


def analyze_crash(exception_name, exception_msg, key_stack, tapd_title, issue_label,
                  device_info=None, tapd_detail=None, tag_list=None, thread_name=None):
    """分析每条崩溃的特征（与原 app.py analyze_crash 一致）
    
    输出三个特征：堆栈特征、TAPD特征、标签特征
    """
    name = (exception_name or '').lower()
    stack = (key_stack or '').lower()
    features = []

    # ===== 特征1：堆栈特征 =====
    stack_parts = []

    # 崩溃类型
    if 'sigsegv' in name or 'segv' in name:
        stack_parts.append('内存访问错误(SIGSEGV)')
    elif 'sigabrt' in name or 'abort' in name:
        stack_parts.append('主动中止(SIGABRT)')
    elif 'sigbus' in name:
        stack_parts.append('总线错误(SIGBUS)')
    elif 'sigfpe' in name:
        stack_parts.append('浮点异常(SIGFPE)')
    elif 'nullpointer' in name:
        stack_parts.append('空指针(NullPointerException)')
    elif 'outofmemory' in name or 'oom' in name:
        stack_parts.append('内存溢出(OOM)')
    elif name:
        stack_parts.append(exception_name[:40])

    # 模块
    so_match = re.search(r'(lib[\w\-]+\.so)', key_stack or '')
    if so_match:
        stack_parts.append(f'模块：{so_match.group(1)}')

    # 线程
    if thread_name:
        stack_parts.append(f'线程：{thread_name}')

    if stack_parts:
        features.append(f'【堆栈特征】{"；".join(stack_parts)}')

    # ===== 特征2：TAPD特征 =====
    if tapd_detail:
        tapd_parts = []
        status = tapd_detail.get('status', '')
        if status:
            tapd_parts.append(f'处理状态：{status}')
        participator = tapd_detail.get('participator', '')
        if participator:
            tapd_parts.append(f'处理人：{participator}')
        desc = tapd_detail.get('description', '')
        if desc:
            tapd_parts.append(f'描述：{desc[:50]}')
        if tapd_parts:
            features.append(f'【TAPD特征】{"；".join(tapd_parts)}')
    elif tapd_title:
        features.append(f'【TAPD特征】标题：{tapd_title[:40]}')

    # ===== 特征3：标签特征 =====
    if tag_list:
        if isinstance(tag_list, list):
            tag_names = []
            for t in tag_list:
                if isinstance(t, dict):
                    tag_names.append(t.get('tagName') or t.get('name') or str(t))
                elif isinstance(t, str):
                    tag_names.append(t)
            if tag_names:
                features.append(f'【标签特征】{"；".join(tag_names[:5])}')

    return '\n'.join(features)
