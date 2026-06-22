"""崩溃率趋势工具"""
from ..config import PROJECTS, CRASHSIGHT_BASE
from ..api_client import openapi_post


def execute(project_id: str, version: str, start_date: str, end_date: str) -> dict:
    """获取崩溃率趋势数据"""
    project = PROJECTS.get(project_id)
    if not project:
        raise ValueError(f"项目不存在: {project_id}")

    app_id = project['appId']
    platform_id = project['pid']
    ver_param = version if version and version != '' else '-1'
    version_list = [v.strip() for v in ver_param.split(',')] if ver_param != '-1' else ['-1']

    # 判断是否为单天（用小时粒度）
    is_single_day = (start_date == end_date)

    if is_single_day:
        body = {
            'appId': app_id,
            'platformId': int(platform_id),
            'startDate': start_date + '00',
            'endDate': end_date + '23',
            'type': 'crash',
            'dataType': 'realTimeTrendData',
            'version': ver_param,
            'mergeMultipleVersionsWithInaccurateResult': False,
            'mergeUserSceneTags': True,
            'mergeVmTypes': True,
            'needCountryDimension': False,
            'needUserSceneTagDimension': False,
            'subModuleId': '-1',
            'countryList': [],
            'vmTypeList': [0],
        }
        data = openapi_post(f'{CRASHSIGHT_BASE}/uniform/openapi/getRealTimeHourlyStatEx', body, timeout=30)
    else:
        body = {
            'appId': app_id,
            'platformId': int(platform_id),
            'startDate': start_date,
            'endDate': end_date,
            'type': 'crash',
            'dataType': 'trendData',
            'vm': 0,
            'versionList': version_list,
            'mergeMultipleVersionsWithInaccurateResult': True,
        }
        data = openapi_post(f'{CRASHSIGHT_BASE}/uniform/openapi/getTrendEx', body, timeout=30)

    return _process_trend(data, is_single_day)


def _process_trend(data: dict, is_single_day: bool) -> dict:
    """解析趋势数据"""
    result = {
        'minRate': '-', 'maxRate': '-', 'avgRate': '-',
        'totalCrash': 0, 'totalAccess': 0, 'totalCrashUser': 0,
        'granularity': 'hour' if is_single_day else 'day',
    }

    # 提取数据点
    items = []
    if isinstance(data, dict):
        ret = data.get('ret', {})
        if isinstance(ret, dict):
            candidates = ret.get('data', [])
            if isinstance(candidates, list) and candidates:
                items = candidates
        if not items:
            d = data.get('data', {})
            if isinstance(d, dict):
                candidates = d.get('data', [])
                if isinstance(candidates, list):
                    items = candidates
            elif isinstance(d, list):
                items = d

    rates = []
    access_list = []
    crash_user_list = []

    for item in items:
        if not isinstance(item, dict):
            continue
        access = item.get('accessUser', 0) or 0
        crash_user = item.get('crashUser', 0) or 0
        crash_num = item.get('crashNum', 0) or 0
        result['totalCrash'] += crash_num
        result['totalAccess'] += access
        result['totalCrashUser'] += crash_user
        if access >= 50:
            rates.append(round(crash_user / access * 100, 4))
            access_list.append(access)
            crash_user_list.append(crash_user)

    if rates:
        result['minRate'] = round(min(rates), 4)
        result['maxRate'] = round(max(rates), 4)
        result['avgRate'] = round(sum(rates) / len(rates), 4)
    if access_list:
        result['minAccess'] = min(access_list)
        result['maxAccess'] = max(access_list)
    if crash_user_list:
        result['minCrashUser'] = min(crash_user_list)
        result['maxCrashUser'] = max(crash_user_list)

    # 逐点数据
    trend_points = []
    for item in items:
        if not isinstance(item, dict):
            continue
        access = item.get('accessUser', 0) or 0
        crash_user = item.get('crashUser', 0) or 0
        rate = round(crash_user / access * 100, 4) if access > 0 else 0
        date_str = item.get('date', '') or item.get('hour', '') or ''
        trend_points.append({'date': date_str, 'crashRate': rate, 'accessUser': access})
    result['trendPoints'] = trend_points

    return result
