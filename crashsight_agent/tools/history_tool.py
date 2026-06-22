"""历史问题判定工具 — 用 LLM 对比堆栈判断是否为同一 Bug"""
import time
from ..config import PROJECTS, CRASHSIGHT_BASE, LLM_MODEL, LLM_API_KEY, LLM_BASE_URL
from ..api_client import openapi_post
from ..llm_client import call_llm


def execute(project_id: str, issue_id: str, exp_stack: str, exp_exception: str = '') -> dict:
    """
    判断体验服问题是否在正式服存在。
    
    流程:
    1. 从堆栈提取搜索关键词
    2. 在正式服搜索候选 issue
    3. 拉取候选堆栈
    4. 用 LLM 判断两个堆栈是否为同一 Bug
    """
    project = PROJECTS.get(project_id)
    if not project:
        raise ValueError(f"项目不存在: {project_id}")

    # 确定搜索目标（体验服→正式服，正式服→搜自己）
    if project['isExperience']:
        target_project_id = project['prod_counterpart']
        if not target_project_id:
            return {'isHistory': False, 'reason': '无对应正式服'}
        target_project = PROJECTS[target_project_id]
    else:
        target_project = project
        target_project_id = project_id

    target_app_id = target_project['appId']
    target_platform_id = target_project['pid']

    # 提取搜索关键词（取堆栈中有意义的函数名）
    search_keyword = _extract_search_keyword(exp_stack, exp_exception)
    if not search_keyword:
        return {'isHistory': False, 'reason': '无法从堆栈提取搜索关键词'}

    # 在正式服搜索候选
    candidates = _search_candidates(target_app_id, target_platform_id, search_keyword, exclude_id=issue_id)
    if not candidates:
        return {'isHistory': False, 'reason': f'正式服未搜到包含"{search_keyword}"的问题'}

    # 逐个候选用 LLM 判断
    for cand in candidates[:3]:
        cand_id = cand.get('issueId', '')
        cand_exception = cand.get('exceptionName', '')

        # 获取候选堆栈
        cand_stack = _get_candidate_stack(cand, target_app_id, target_platform_id)
        if not cand_stack:
            continue

        # LLM 判断
        is_same = _llm_compare_stacks(exp_stack, cand_stack, exp_exception, cand_exception)
        if is_same:
            prod_url = f'https://crashsight.qq.com/crash-reporting/crashes/{target_app_id}/{cand_id}?pid={target_platform_id}'
            return {
                'isHistory': True,
                'prodIssueId': cand_id,
                'prodException': cand_exception,
                'prodUrl': prod_url,
                'prodCrashCount': cand.get('crashNum') or cand.get('count') or 0,
                'prodAffectedUsers': cand.get('imeiCount', 0),
            }

        time.sleep(1)

    return {'isHistory': False, 'reason': '候选堆栈均不匹配'}


def _extract_search_keyword(stack: str, exception_name: str = '') -> str:
    """从堆栈提取搜索关键词（简化版：取第一个有意义的 Class::method）"""
    import re
    if not stack:
        return exception_name.split('.')[-1] if exception_name else ''

    # 跳过噪音帧
    skip = (
        'libc.so', 'libart.so', 'abort', 'raise', 'tgkill',
        'FDebug::', 'FPlatformMisc::', 'FOutputDevice::',
        'StaticFailDebug', 'CommonUnixCrashHandler',
        '__pthread', '__kernel', 'signal handler',
    )

    for line in stack.split('\n'):
        line = line.strip()
        if not line:
            continue
        if any(s in line for s in skip):
            continue
        # C++ Class::method
        m = re.search(r'(\w+(?:<[^>]*>)?::\w+)\s*\(', line)
        if m and len(m.group(1)) > 8:
            return m.group(1)
        # Java
        m = re.search(r'([\w$]+\.[\w$]+)\(', line)
        if m and len(m.group(1)) > 8:
            return m.group(1)

    return exception_name.split('.')[-1] if exception_name else ''


def _search_candidates(app_id: str, platform_id: int, keyword: str, exclude_id: str = '', limit: int = 5) -> list:
    """在目标项目搜索候选 issue"""
    conditions = [
        {'queryType': 'TERMS_WILDCARD', 'field': 'version', 'terms': ['1.*']},
        {'field': 'exceptionType'},
        {'queryType': 'TEXT_MATCH_PHRASE', 'field': 'crashDetail', 'text': keyword, 'not': False},
    ]

    body = {
        'appId': app_id,
        'platformId': int(platform_id),
        'pid': str(platform_id),
        'desc': 'true',
        'rows': limit,
        'start': '0',
        'sortField': 'matchCount',
        'searchConditionGroup': {'conditions': conditions},
    }

    try:
        data = openapi_post(f'{CRASHSIGHT_BASE}/uniform/openapi/advancedSearchEx', body, timeout=15)
        inner = data.get('data', {}) if isinstance(data.get('data'), dict) else {}
        if not inner:
            inner = data.get('ret', {}) if isinstance(data.get('ret'), dict) else {}
        issue_list = inner.get('issueList', []) or []

        # 排除自己
        if exclude_id:
            issue_list = [i for i in issue_list if not i.get('issueId', '').startswith(exclude_id[:8])]

        return issue_list
    except Exception as e:
        print(f'[History] 搜索失败: {e}')
        return []


def _get_candidate_stack(cand: dict, app_id: str, platform_id: int) -> str:
    """从候选中获取堆栈（优先 lastMatchedReport，不行再调 API）"""
    # 先从 lastMatchedReport 取
    lmr = cand.get('lastMatchedReport', {}) or {}
    cm = lmr.get('crashMap', {}) or {} if isinstance(lmr, dict) else {}
    if isinstance(cm, dict):
        stack = cm.get('retraceCrashDetail', '') or cm.get('callStack', '')
        if stack:
            return stack

    # 调 API 获取
    cand_id = cand.get('issueId', '')
    if not cand_id:
        return ''

    try:
        body = {
            'appId': app_id,
            'platformId': int(platform_id),
            'pid': int(platform_id),
            'issueId': cand_id,
            'crashDataType': 'undefined',
            'searchType': 'detail',
            'exceptionTypeList': 'Crash,Native,ExtensionCrash',
            'rows': 1,
            'start': 0,
            'version': '1.*',
        }
        data = openapi_post(f'{CRASHSIGHT_BASE}/uniform/openapi/crashList', body, timeout=15)
        crash_id_list = data.get('ret', {}).get('crashIdList', [])
        if not crash_id_list:
            return ''

        body2 = {'appId': app_id, 'platformId': str(platform_id), 'crashHash': crash_id_list[0]}
        data2 = openapi_post(f'{CRASHSIGHT_BASE}/uniform/openapi/crashDoc', body2, timeout=15)
        crash_map = data2.get('ret', {}).get('crashMap', {})
        return crash_map.get('retraceCrashDetail', '') or crash_map.get('callStack', '')
    except Exception as e:
        print(f'[History] 获取候选堆栈失败: {e}')
        return ''


def _llm_compare_stacks(stack_a: str, stack_b: str, exc_a: str, exc_b: str) -> bool:
    """用 LLM 判断两个崩溃堆栈是否为同一 Bug"""
    # 截断堆栈避免 token 过长
    stack_a_short = '\n'.join(stack_a.split('\n')[:30])
    stack_b_short = '\n'.join(stack_b.split('\n')[:30])

    prompt = f"""你是一个崩溃分析专家。请判断以下两个崩溃堆栈是否属于同一个 Bug（同一根因）。

## 堆栈 A（体验服）
异常类型: {exc_a}
```
{stack_a_short}
```

## 堆栈 B（正式服候选）
异常类型: {exc_b}
```
{stack_b_short}
```

## 判断规则
1. 如果异常类型完全不同（如 SIGSEGV vs SIGABRT），通常不是同一 Bug
2. 关注崩溃的根因函数（真正出错的那一帧），而不是底层框架帧
3. 允许编译器优化导致的帧序微小变化
4. 不同版本之间，同一 Bug 的堆栈可能有少量差异，但核心调用链应该一致

请只回答 YES 或 NO，然后用一句话说明理由。
格式: YES/NO: 理由"""

    response = call_llm(prompt)
    if not response:
        return False

    answer = response.strip().upper()
    is_same = answer.startswith('YES')
    print(f'[History-LLM] 判定: {"Match" if is_same else "Mismatch"} | {response[:80]}')
    return is_same
