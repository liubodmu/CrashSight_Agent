"""项目名模糊匹配 — 将用户口语化的项目名解析为 project_id"""
from ..config import PROJECTS


def resolve_project(text: str) -> str:
    """
    从文本中解析项目 ID
    
    规则:
    - 精确匹配 aliases
    - 模糊匹配: "安卓" 默认 android_exp，"iOS" 默认 ios_exp
    - 如果包含"正式"，选 prod；否则选 exp
    """
    text_lower = text.lower()

    # 先精确匹配 aliases
    for pid, config in PROJECTS.items():
        for alias in config.get('aliases', []):
            if alias.lower() in text_lower:
                return pid

    # 模糊匹配平台 + 类型
    is_prod = '正式' in text or 'prod' in text_lower
    
    if '安卓' in text or 'android' in text_lower:
        return 'android_prod' if is_prod else 'android_exp'
    
    if 'ios' in text_lower or '苹果' in text or 'iPhone' in text_lower:
        return 'ios_prod' if is_prod else 'ios_exp'
    
    if '鸿蒙' in text or 'harmony' in text_lower:
        return 'harmony_prod' if is_prod else 'harmony_exp'

    return ''  # 无法识别
