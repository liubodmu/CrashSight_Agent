"""TAPD 缺陷单详情工具"""
import re
import requests
from ..config import TAPD_TOKEN, TAPD_API, CRASHSIGHT_BASE, PROJECTS
from ..api_client import openapi_post


def execute(workspace_id: str, bug_id: str, app_id: str = '', platform_id: int = 1) -> dict:
    """获取 TAPD 缺陷单详情"""
    if not workspace_id or not bug_id:
        return {'error': '缺少 workspace_id 或 bug_id'}

    # 用 CrashSight queryBugs 接口获取基本信息
    _app_id = app_id or PROJECTS.get('android_exp', {}).get('appId', '')
    try:
        data = openapi_post(f'{CRASHSIGHT_BASE}/uniform/openapi/queryBugs', {
            'appId': _app_id,
            'platformId': int(platform_id),
            'bugInfos': [{'bugPlatform': 'TAPD', 'id': bug_id}]
        }, timeout=10)
        bugs = data.get('data', []) if isinstance(data, dict) else []
        bug = bugs[0] if bugs else {}
    except:
        bug = {}

    # 获取评论
    comments = []
    try:
        headers = {'Authorization': f'Bearer {TAPD_TOKEN}'}
        resp = requests.get(
            f'{TAPD_API}/comments?workspace_id={workspace_id}&entry_type=bug&entry_id={bug_id}&limit=10',
            headers=headers, timeout=8, verify=False
        )
        if resp.status_code == 200:
            for c in resp.json().get('data', []):
                comment = c.get('Comment', {})
                text = _clean_html(comment.get('description', ''))
                if text and len(text) > 5:
                    comments.append(text[:200])
    except:
        pass

    return {
        'title': bug.get('title', ''),
        'status': _translate_status(bug.get('status', '')),
        'participator': bug.get('participator', ''),
        'description': _clean_html(bug.get('description', ''))[:300],
        'comments': comments[:5],
        'url': f'https://tapd.woa.com/tapd_fe/{workspace_id}/bug/detail/{bug_id}',
    }


def _translate_status(status: str) -> str:
    """翻译 TAPD 状态"""
    status_map = {
        'new': '新建',
        'open': '已打开',
        'in_progress': '处理中',
        'resolved': '已解决',
        'verified': '已验证',
        'closed': '已关闭',
        'rejected': '已拒绝',
        'reopened': '重新打开',
    }
    return status_map.get(status.lower(), status) if status else '未知'


def _clean_html(html_text: str) -> str:
    """清理 HTML 标签"""
    if not html_text:
        return ''
    text = re.sub(r'<[^>]+>', ' ', html_text)
    text = re.sub(r'&nbsp;|&amp;|&lt;|&gt;', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text
