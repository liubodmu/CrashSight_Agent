"""CrashSight Agent Tools — 工具注册、熔断器、统一执行"""
import time
from .trend_tool import execute as exec_trend
from .top_issues_tool import execute as exec_top_issues
from .stack_tool import execute as exec_stack
from .history_tool import execute as exec_history
from .tapd_tool import execute as exec_tapd
from .report_tool import execute as exec_report
from .circuit_breaker import CircuitBreaker, classify_error, RETRY_POLICY, ErrorType

# ==================== 工具注册表 ====================

TOOL_EXECUTORS = {
    'get_crash_trend': exec_trend,
    'get_top_issues': exec_top_issues,
    'get_issue_full_stack': exec_stack,
    'check_history_issue': exec_history,
    'get_tapd_bug_detail': exec_tapd,
    'generate_crash_report': exec_report,
}

# 每个工具独立的熔断器
_circuit_breakers = {name: CircuitBreaker(failure_threshold=3, recovery_timeout=60) for name in TOOL_EXECUTORS}

# ==================== 给 LLM 的 Function Calling Schema ====================

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_crash_trend",
            "description": "获取指定项目、版本、时间范围的崩溃率趋势数据。返回每日崩溃率(最低/最高/平均)、联网设备数、影响设备数。",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {
                        "type": "string",
                        "enum": ["android_exp", "android_prod", "ios_exp", "ios_prod", "harmony_exp", "harmony_prod"],
                        "description": "项目ID"
                    },
                    "version": {"type": "string", "description": "版本号，支持通配符如'3.7.*'，全版本用'-1'"},
                    "start_date": {"type": "string", "description": "开始日期，格式YYYYMMDD"},
                    "end_date": {"type": "string", "description": "结束日期，格式YYYYMMDD"}
                },
                "required": ["project_id", "version", "start_date", "end_date"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_top_issues",
            "description": "获取TOP N崩溃问题列表。返回每个问题的issueId、异常名、崩溃次数、影响用户数、关键堆栈。",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string", "enum": ["android_exp", "android_prod", "ios_exp", "ios_prod", "harmony_exp", "harmony_prod"]},
                    "version": {"type": "string", "description": "版本号"},
                    "start_date": {"type": "string", "description": "开始日期YYYYMMDD"},
                    "end_date": {"type": "string", "description": "结束日期YYYYMMDD"},
                    "top_n": {"type": "integer", "description": "返回数量，默认10", "default": 10}
                },
                "required": ["project_id", "version", "start_date", "end_date"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_issue_full_stack",
            "description": "获取某个issue最新一次崩溃的完整堆栈和设备信息。",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string", "description": "项目ID"},
                    "issue_id": {"type": "string", "description": "CrashSight issueId"},
                    "version": {"type": "string", "description": "版本号，可选", "default": "-1"}
                },
                "required": ["project_id", "issue_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "check_history_issue",
            "description": "判断某个崩溃问题是否在正式服/历史版本中出现过（是历史问题还是新问题）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string", "description": "项目ID"},
                    "issue_id": {"type": "string", "description": "issueId"},
                    "exp_stack": {"type": "string", "description": "体验服堆栈"},
                    "exp_exception": {"type": "string", "description": "异常名"}
                },
                "required": ["project_id", "issue_id", "exp_stack"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_tapd_bug_detail",
            "description": "获取TAPD缺陷单详情（标题、状态、描述摘要、评论）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "workspace_id": {"type": "string", "description": "TAPD workspace ID"},
                    "bug_id": {"type": "string", "description": "TAPD bug ID"}
                },
                "required": ["workspace_id", "bug_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "generate_crash_report",
            "description": "根据已收集的趋势数据和TOP问题列表，生成完整的Markdown崩溃分析报告。",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string", "description": "项目ID"},
                    "version": {"type": "string", "description": "版本号"},
                    "start_date": {"type": "string", "description": "开始日期"},
                    "end_date": {"type": "string", "description": "结束日期"},
                    "trend_data": {"type": "object", "description": "崩溃率趋势数据"},
                    "top_issues": {"type": "array", "description": "TOP问题列表"}
                },
                "required": ["project_id", "version", "start_date", "end_date"]
            }
        }
    },
]


# ==================== 统一执行入口（带熔断器 + 错误反思 + 参数调整重试）====================

def execute_tool(name: str, args: dict) -> dict:
    """
    统一工具执行入口，内置:
    1. 熔断器检查（连续失败 3 次自动跳过）
    2. 错误分类（区分限流/超时/404/参数错误）
    3. **错误反思**（分析错误原因，智能调整参数后重试）
    4. 降级返回（熔断时返回友好提示）
    
    与简单重试的区别：
    - 简单重试：同样参数再来一次（只对限流/超时有效）
    - 错误反思：理解"为什么错" → 调整参数 → 用新参数重试
    """
    fn = TOOL_EXECUTORS.get(name)
    if not fn:
        return {'success': False, 'error': f'未知工具: {name}', 'error_type': 'unknown'}

    breaker = _circuit_breakers[name]

    # 熔断器检查
    if not breaker.can_execute():
        print(f'[Tool] {name} 已熔断，跳过执行 (连续失败{breaker.failure_count}次，{breaker.recovery_timeout}s后恢复)')
        return {
            'success': False,
            'error': f'{name} 暂时不可用（连续失败已熔断，{breaker.recovery_timeout}s后自动恢复）',
            'error_type': 'circuit_open',
            'degraded': True,
        }

    # 执行（带反思重试）
    last_error = ''
    current_args = dict(args)  # 可变参数副本
    max_attempts = 3

    for attempt in range(max_attempts):
        try:
            result = fn(**current_args)
            # 成功
            breaker.record_success()
            return {'success': True, 'data': result}
        except Exception as e:
            last_error = str(e)
            error_type = classify_error(last_error)
            policy = RETRY_POLICY[error_type]

            print(f'[Tool] {name} 失败 (attempt {attempt+1}): [{error_type.value}] {last_error[:80]}')

            # ─── 错误反思：尝试理解错误并调整参数 ───
            adjusted_args = _reflect_and_adjust(name, current_args, last_error, error_type, attempt)

            if adjusted_args is not None:
                # 反思成功，用新参数重试
                old_diff = {k: v for k, v in adjusted_args.items() if current_args.get(k) != v}
                print(f'[Tool] 🔍 反思调整: {old_diff}')
                current_args = adjusted_args
                if policy['wait_seconds'] > 0:
                    time.sleep(policy['wait_seconds'])
                continue

            # 无法反思调整 → 判断是否简单重试
            if not policy['should_retry'] or attempt >= policy['max_retries']:
                breaker.record_failure()
                return {
                    'success': False,
                    'error': last_error,
                    'error_type': error_type.value,
                    'retried': attempt,
                }

            # 简单重试（限流/超时等）
            if policy['wait_seconds'] > 0:
                wait = policy['wait_seconds'] * (attempt + 1)
                print(f'[Tool] 等待 {wait}s 后重试（原参数）...')
                time.sleep(wait)

    breaker.record_failure()
    return {'success': False, 'error': last_error, 'error_type': 'unknown', 'retried': max_attempts}


# ==================== 错误反思引擎 ====================

def _reflect_and_adjust(tool_name: str, args: dict, error: str, error_type: ErrorType, attempt: int) -> dict:
    """
    分析错误原因，智能调整参数
    
    返回: 调整后的新 args（如果能调整），或 None（无法调整）
    
    这是 Agent "反思" 能力在工具层的体现：
    不是简单重试，而是理解"为什么错"→ 针对性修改参数
    """
    error_lower = error.lower()
    new_args = dict(args)

    # ═══════ get_crash_trend 的反思 ═══════
    if tool_name == 'get_crash_trend':
        # 场景1: 版本不存在 → 改为全版本
        if ('version' in error_lower or '不存在' in error_lower or 'empty' in error_lower):
            if args.get('version') and args['version'] != '-1':
                new_args['version'] = '-1'
                print(f'[Reflect] 趋势: 版本 {args["version"]} 无数据，切换全版本')
                return new_args

        # 场景2: 日期范围无数据 → 缩短范围
        if '无数据' in error or 'empty' in error_lower:
            start = args.get('start_date', '')
            end = args.get('end_date', '')
            if start and end and len(start) == 8 and start != end:
                # 缩短到最近 3 天
                from datetime import datetime, timedelta
                end_dt = datetime.strptime(end, '%Y%m%d')
                new_start = (end_dt - timedelta(days=2)).strftime('%Y%m%d')
                if new_start > start:
                    new_args['start_date'] = new_start
                    print(f'[Reflect] 趋势: 日期范围过大无数据，缩短为 {new_start}~{end}')
                    return new_args

    # ═══════ get_top_issues 的反思 ═══════
    elif tool_name == 'get_top_issues':
        # 场景1: 版本无数据 → 用通配版本
        if args.get('version') and args['version'] != '-1' and '.*' not in args['version']:
            # 精确版本无数据 → 改为前缀通配
            parts = args['version'].split('.')
            if len(parts) >= 2:
                new_args['version'] = f'{parts[0]}.{parts[1]}.*'
                print(f'[Reflect] TOP issues: 精确版本无数据，改为通配 {new_args["version"]}')
                return new_args

        # 场景2: 通配版本也无数据 → 全版本
        if args.get('version') and args['version'] != '-1':
            new_args['version'] = '-1'
            print(f'[Reflect] TOP issues: 版本 {args["version"]} 无数据，切换全版本')
            return new_args

    # ═══════ get_issue_full_stack 的反思 ═══════
    elif tool_name == 'get_issue_full_stack':
        # 场景1: crashIdList 为空 → 可能是 crashDataType 不对
        if 'crashidlist' in error_lower or '为空' in error:
            if attempt == 0:
                # 第一次失败：尝试加上所有异常类型
                new_args['crash_data_type'] = 'all'
                print(f'[Reflect] 堆栈: crashIdList为空，尝试扩大异常类型')
                return new_args
            elif attempt == 1:
                # 第二次失败：尝试用 version=-1
                if args.get('version') and args['version'] != '-1':
                    new_args['version'] = '-1'
                    print(f'[Reflect] 堆栈: 仍为空，去掉版本限制')
                    return new_args

    # ═══════ check_history_issue 的反思 ═══════
    elif tool_name == 'check_history_issue':
        # 场景1: 搜索结果过多(>5000) 但匹配度全为0 → 关键帧太泛
        if '关键帧太泛' in error or '搜索结果过多' in error:
            # 截取更具体的关键帧（取更多字符）
            exp_stack = args.get('exp_stack', '')
            if exp_stack:
                lines = [l.strip() for l in exp_stack.split('\n') if l.strip()]
                # 尝试用第 2-3 行作为新的搜索关键词
                if len(lines) >= 3:
                    new_args['_use_secondary_frames'] = True
                    print(f'[Reflect] 历史判定: 关键帧太泛，切换到次要特征帧')
                    return new_args

        # 场景2: 正式服搜索超时 → 缩小搜索范围
        if 'timeout' in error_lower:
            new_args['_reduce_search_limit'] = True
            print(f'[Reflect] 历史判定: 搜索超时，缩小搜索范围')
            return new_args

    # ═══════ get_tapd_bug_detail 的反思 ═══════
    elif tool_name == 'get_tapd_bug_detail':
        # 场景1: workspace_id 或 bug_id 格式错误
        if '400' in error or 'invalid' in error_lower:
            # TAPD 参数错误无法自动修复
            print(f'[Reflect] TAPD: 参数错误，无法自动修复')
            return None

        # 场景2: 403 权限不足 → 不重试
        if '403' in error or 'forbidden' in error_lower:
            print(f'[Reflect] TAPD: 权限不足，跳过')
            return None

    # ═══════ 通用反思规则 ═══════

    # 规则A: 任何工具返回 "数据不存在/empty" 且有 version 参数 → 放宽版本
    if ('不存在' in error or 'empty' in error_lower or 'no data' in error_lower):
        if 'version' in args and args['version'] not in ('-1', None, ''):
            if attempt == 0 and '.*' not in str(args.get('version', '')):
                parts = str(args['version']).split('.')
                if len(parts) >= 2:
                    new_args['version'] = f'{parts[0]}.{parts[1]}.*'
                    print(f'[Reflect] 通用: 数据不存在，放宽版本为 {new_args["version"]}')
                    return new_args

    # 规则B: 超时且有 timeout 参数 → 增加超时时间
    if error_type == ErrorType.TIMEOUT:
        timeout = args.get('timeout', 15)
        if timeout < 30:
            new_args['timeout'] = min(timeout * 2, 60)
            print(f'[Reflect] 通用: 超时，增加 timeout {timeout}→{new_args["timeout"]}s')
            return new_args

    # 规则C: 限流（429）→ 不调整参数，由上层等待处理
    if error_type == ErrorType.RATE_LIMIT:
        return None  # 交给简单重试（等待后原参数重试）

    # 规则D: 鉴权错误 → 无法自动修复
    if error_type == ErrorType.AUTH_ERROR:
        return None

    return None  # 无法反思调整
