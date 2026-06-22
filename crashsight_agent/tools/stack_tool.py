"""完整堆栈获取工具"""
import time
from ..config import PROJECTS, CRASHSIGHT_BASE
from ..api_client import openapi_post
from ..context import WindowManager

_window = WindowManager()


def execute(project_id: str, issue_id: str, version: str = '-1') -> dict:
    """获取 issue 最新一次崩溃的完整堆栈和设备信息"""
    project = PROJECTS.get(project_id)
    if not project:
        raise ValueError(f"项目不存在: {project_id}")

    app_id = project['appId']
    platform_id = project['pid']

    # 第一步：获取最新 crashHash
    body1 = {
        'appId': app_id,
        'platformId': int(platform_id),
        'pid': int(platform_id),
        'issueId': issue_id,
        'crashDataType': 'undefined',
        'searchType': 'detail',
        'exceptionTypeList': 'Crash,Native,ExtensionCrash',
        'rows': 1,
        'start': 0,
    }
    if version and version != '-1':
        body1['version'] = version

    data1 = openapi_post(f'{CRASHSIGHT_BASE}/uniform/openapi/crashList', body1, timeout=15)
    ret = data1.get('ret', {})
    crash_id_list = ret.get('crashIdList', [])
    crash_datas = ret.get('crashDatas', {})

    if not crash_id_list:
        return {'callStack': '', 'rawCallStack': '', 'error': 'crashIdList为空'}

    crash_hash = crash_id_list[0]
    crash_info = crash_datas.get(crash_hash, {})

    # 第二步：获取完整堆栈
    body2 = {'appId': app_id, 'platformId': str(platform_id), 'crashHash': crash_hash}
    data2 = openapi_post(f'{CRASHSIGHT_BASE}/uniform/openapi/crashDoc', body2, timeout=15)
    crash_map = data2.get('ret', {}).get('crashMap', {})

    retrace_stack = crash_map.get('retraceCrashDetail', '')
    raw_stack = crash_map.get('callStack', '') or crash_info.get('callStack', '')
    call_stack = retrace_stack or raw_stack

    # Context 工程: 智能截断过长堆栈（保留崩溃点 + 调用入口）
    call_stack_truncated = _window.truncate_stack(call_stack, max_tokens=1500)

    return {
        'callStack': call_stack_truncated,
        'callStackFull': call_stack,         # 保留完整版（history_tool 对比用）
        'rawCallStack': raw_stack,
        'crashHash': crash_hash,
        'brand': crash_info.get('brand', ''),
        'model': crash_info.get('model', ''),
        'cpuName': crash_info.get('cpuName', ''),
        'osVersion': crash_info.get('osVer', ''),
        'threadName': crash_map.get('threadName', ''),
    }
