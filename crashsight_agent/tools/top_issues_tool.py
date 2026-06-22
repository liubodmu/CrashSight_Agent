"""TOP 崩溃问题列表工具"""
import time
from datetime import datetime
from ..config import PROJECTS, CRASHSIGHT_BASE
from ..api_client import openapi_post


def execute(project_id: str, version: str, start_date: str, end_date: str, top_n: int = 10) -> list:
    """获取 TOP N 崩溃问题列表"""
    project = PROJECTS.get(project_id)
    if not project:
        raise ValueError(f"项目不存在: {project_id}")

    app_id = project['appId']
    platform_id = project['pid']
    ver_param = version if version and version != '' else '-1'
    v_list = [v.strip() for v in ver_param.split(';')] if ver_param != '-1' else []

    # 构造 searchConditionGroup（与 CrashSight 网页控制台一致）
    conditions = []

    # 版本过滤
    if v_list:
        conditions.append({
            'queryType': 'TERMS_WILDCARD',
            'field': 'version',
            'terms': v_list,
        })

    # 时间过滤
    try:
        start_dt = datetime.strptime(start_date, '%Y%m%d')
        end_dt = datetime.strptime(end_date, '%Y%m%d').replace(hour=23, minute=59, second=59)
        duration_ms = int((end_dt - start_dt).total_seconds() * 1000) + 1000
        conditions.append({
            'field': 'crashUploadTime',
            'queryType': 'RANGE_RELATIVE_DATETIME',
            'gte': duration_ms,
        })
    except Exception as e:
        print(f'[TopIssues] 时间解析失败: {e}')

    conditions.append({'field': 'exceptionType'})
    conditions.append({'field': 'crashDetail'})

    body = {
        'appId': app_id,
        'platformId': int(platform_id),
        'pid': str(platform_id),
        'rows': top_n * 2,  # 多拉一些，后面排序截取
        'start': '0',
        'sortField': 'matchCount',
        'desc': 'true',
        'enableSearchOomInAdvancedSearch': True,
        'oomDesc': True,
        'oomRows': 10,
        'oomSortField': 'uploadTimestamp',
        'oomStart': 0,
        'searchConditionGroup': {'conditions': conditions},
    }

    data = openapi_post(f'{CRASHSIGHT_BASE}/uniform/openapi/advancedSearchEx', body, timeout=45)

    # 解析响应
    inner = {}
    if isinstance(data, dict):
        inner = data.get('data', {}) if isinstance(data.get('data'), dict) else {}
        if not inner:
            inner = data.get('ret', {}) if isinstance(data.get('ret'), dict) else {}

    issues = inner.get('issueList', [])
    if not isinstance(issues, list):
        issues = []

    # 整理结果
    result = []
    total_crash = 0
    for issue in issues:
        if not issue.get('issueId'):
            continue
        crash_count = issue.get('crashNum') or issue.get('count') or issue.get('crashCount') or 0
        affected = issue.get('imeiCount') or issue.get('crashUser') or 0
        total_crash += crash_count

        tapd_list = issue.get('bugs') or []
        tapd = tapd_list[0] if tapd_list else None

        result.append({
            'issueId': issue.get('issueId', ''),
            'exceptionName': issue.get('exceptionName', ''),
            'crashCount': crash_count,
            'affectedUsers': affected,
            'keyStack': issue.get('keyStack', ''),
            'firstCrashVersion': issue.get('firstCrashVersion', ''),
            'firstUploadTime': issue.get('firstUploadTime', ''),
            'lastUploadTime': issue.get('lastestUploadTime', ''),
            'tapdBug': {
                'workspaceId': tapd.get('workspaceId', ''),
                'id': tapd.get('id', ''),
                'title': tapd.get('title', ''),
            } if tapd else None,
        })

    # 按影响用户数排序
    result.sort(key=lambda x: x['affectedUsers'], reverse=True)

    # 计算占比
    for item in result:
        if total_crash > 0 and item['crashCount'] > 0:
            item['crashRatio'] = round(item['crashCount'] / total_crash * 100, 2)
        else:
            item['crashRatio'] = 0

    return result[:top_n]
