"""CrashSight Agent Tools — 工具注册与执行"""
from .trend_tool import execute as exec_trend
from .top_issues_tool import execute as exec_top_issues
from .stack_tool import execute as exec_stack
from .history_tool import execute as exec_history
from .tapd_tool import execute as exec_tapd
from .report_tool import execute as exec_report

TOOL_EXECUTORS = {
    'get_crash_trend': exec_trend,
    'get_top_issues': exec_top_issues,
    'get_issue_full_stack': exec_stack,
    'check_history_issue': exec_history,
    'get_tapd_bug_detail': exec_tapd,
    'generate_crash_report': exec_report,
}

# 给 LLM 的 Function Calling 工具描述
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
                    "version": {
                        "type": "string",
                        "description": "版本号，支持通配符如'3.7.*'，全版本用'-1'"
                    },
                    "start_date": {
                        "type": "string",
                        "description": "开始日期，格式YYYYMMDD"
                    },
                    "end_date": {
                        "type": "string",
                        "description": "结束日期，格式YYYYMMDD"
                    }
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
                    "project_id": {
                        "type": "string",
                        "enum": ["android_exp", "android_prod", "ios_exp", "ios_prod", "harmony_exp", "harmony_prod"],
                        "description": "项目ID"
                    },
                    "version": {
                        "type": "string",
                        "description": "版本号"
                    },
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
            "description": "判断某个崩溃问题是否在正式服/历史版本中出现过（是历史问题还是新问题）。需要提供两个堆栈让LLM对比。",
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


def execute_tool(name: str, args: dict) -> dict:
    """统一工具执行入口"""
    fn = TOOL_EXECUTORS.get(name)
    if not fn:
        return {'success': False, 'error': f'未知工具: {name}'}
    try:
        result = fn(**args)
        return {'success': True, 'data': result}
    except Exception as e:
        return {'success': False, 'error': str(e)}
