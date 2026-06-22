"""多策略 Ensemble — 三路投票判定历史问题

路线A: 硬锚点算法（关键帧+下方10帧重合度）
路线B: Jaccard 函数名集合相似度
路线C: LLM 语义判断

投票规则:
- A+B 都同意(2:0) → 直接 Match（不调 LLM，省钱）
- A+B 都反对(0:2) → 直接 Mismatch（不调 LLM）
- A+B 不一致(1:1) → 调 LLM 仲裁
- 最终: 3路中2路同意 → Match
"""
import re
from .keyframe import extract_key_frame
from ..llm_client import call_llm


def ensemble_compare(stack_a: str, stack_b: str, exc_a: str, exc_b: str, key_frame: str) -> dict:
    """三路投票判定两个堆栈是否为同一 Bug
    
    返回:
    {
        'is_match': True/False,
        'votes': {'anchor': True/False, 'jaccard': True/False, 'llm': True/False/None},
        'scores': {'anchor_overlap': 0.55, 'jaccard': 0.73},
        'llm_reason': '...',
        'strategy': 'fast_match' / 'fast_reject' / 'llm_arbitrate',
    }
    """
    # ── 路线 A: 硬锚点 ──
    anchor_result, anchor_overlap = _hard_anchor_match(stack_a, stack_b, key_frame, exc_a, exc_b)

    # ── 路线 B: Jaccard ──
    jaccard_result, jaccard_score = _jaccard_match(stack_a, stack_b)

    # ── 快速通道: A+B 一致时不调 LLM ──
    if anchor_result and jaccard_result:
        # 2:0 → 快速 Match
        return {
            'is_match': True,
            'votes': {'anchor': True, 'jaccard': True, 'llm': None},
            'scores': {'anchor_overlap': anchor_overlap, 'jaccard': jaccard_score},
            'llm_reason': '',
            'strategy': 'fast_match',
        }

    if not anchor_result and not jaccard_result:
        # 0:2 → 快速 Reject
        return {
            'is_match': False,
            'votes': {'anchor': False, 'jaccard': False, 'llm': None},
            'scores': {'anchor_overlap': anchor_overlap, 'jaccard': jaccard_score},
            'llm_reason': '',
            'strategy': 'fast_reject',
        }

    # ── 1:1 冲突: 调 LLM 仲裁 ──
    llm_result, llm_reason = _llm_compare(stack_a, stack_b, exc_a, exc_b, key_frame)

    # 最终: 2路同意 → Match
    votes_true = sum([anchor_result, jaccard_result, llm_result])
    is_match = votes_true >= 2

    return {
        'is_match': is_match,
        'votes': {'anchor': anchor_result, 'jaccard': jaccard_result, 'llm': llm_result},
        'scores': {'anchor_overlap': anchor_overlap, 'jaccard': jaccard_score},
        'llm_reason': llm_reason,
        'strategy': 'llm_arbitrate',
    }


# ==================== 路线 A: 硬锚点匹配 ====================

def _hard_anchor_match(stack_a: str, stack_b: str, key_frame: str,
                       exc_a: str = '', exc_b: str = '',
                       window_size: int = 10, threshold: float = 0.5) -> tuple:
    """硬锚点法: 关键帧下方 N 帧函数名重合度
    
    返回: (is_match: bool, overlap: float)
    """
    # 异常类型检查
    if exc_a and exc_b:
        if not _exception_compatible(exc_a, exc_b):
            return False, 0.0

    if not key_frame or not stack_a or not stack_b:
        return False, 0.0

    # 提取关键帧下方的函数名列表
    funcs_a = _extract_funcs_below_anchor(stack_a, key_frame, window_size)
    funcs_b = _extract_funcs_below_anchor(stack_b, key_frame, window_size)

    if funcs_a is None or funcs_b is None:
        return False, 0.0

    # 两边都为空（关键帧在栈底）→ 仅锚点一致算 Match
    if not funcs_a and not funcs_b:
        return True, 1.0

    if not funcs_a or not funcs_b:
        return False, 0.0

    # 计算重合度
    intersection = set(funcs_a) & set(funcs_b)
    denom = max(len(funcs_a), len(funcs_b))
    overlap = len(intersection) / denom if denom else 0.0

    return overlap >= threshold, round(overlap, 3)


def _extract_funcs_below_anchor(stack: str, anchor: str, window_size: int) -> list:
    """提取关键帧下方 window_size 帧的函数名"""
    lines = [l.strip() for l in stack.split('\n') if l.strip()]
    anchor_idx = -1
    for i, line in enumerate(lines):
        if anchor in line:
            anchor_idx = i
            break
    if anchor_idx < 0:
        return None

    below = lines[anchor_idx + 1: anchor_idx + 1 + window_size]
    funcs = []
    for line in below:
        # C++ Class::method(
        m = re.search(r'(\w+(?:<[^>]*>)?)::(\w+)\s*\(', line)
        if m and len(m.group(2)) > 1:
            funcs.append(f'{m.group(1)}::{m.group(2)}')
            continue
        # Java xx.yy(
        m = re.search(r'([\w$]+)\.([\w$]+)\(', line)
        if m and len(m.group(2)) > 1:
            funcs.append(m.group(2))
            continue
        # 全局函数 .so funcName(
        m = re.search(r'\.so\s+([a-zA-Z_][\w]{2,})\s*\(', line)
        if m:
            funcs.append(m.group(1))
    return funcs


def _exception_compatible(exc_a: str, exc_b: str) -> bool:
    """判断两个异常类型是否兼容（可能是同一Bug）"""
    if exc_a == exc_b:
        return True
    # 内存访问类互相兼容
    mem_signals = {'SIGSEGV', 'SIGBUS'}
    sig_a = re.match(r'(SIG\w+)', exc_a)
    sig_b = re.match(r'(SIG\w+)', exc_b)
    if sig_a and sig_b:
        sa, sb = sig_a.group(1), sig_b.group(1)
        if sa in mem_signals and sb in mem_signals:
            return True
        # SIGABRT 只和自己兼容
        if sa == 'SIGABRT' or sb == 'SIGABRT':
            return False
    return True


# ==================== 路线 B: Jaccard 函数名集合 ====================

def _jaccard_match(stack_a: str, stack_b: str, threshold: float = 0.7) -> tuple:
    """Jaccard 相似度: 两个堆栈的有意义函数名集合重合度
    
    返回: (is_match: bool, score: float)
    """
    funcs_a = _extract_meaningful_funcs(stack_a)
    funcs_b = _extract_meaningful_funcs(stack_b)

    if not funcs_a or not funcs_b:
        return False, 0.0

    intersection = len(funcs_a & funcs_b)
    union = len(funcs_a | funcs_b)
    score = intersection / union if union else 0.0

    return score >= threshold, round(score, 3)


def _extract_meaningful_funcs(stack: str) -> set:
    """从堆栈提取有意义的函数名集合（排除通用框架函数）"""
    if not stack:
        return set()

    _GENERIC = {
        'abort', 'raise', 'memcpy', 'memset', 'malloc', 'free',
        'pthread_mutex_lock', 'start_thread',
    }
    _GENERIC_PREFIXES = (
        'FRunnableThread', 'FNamedTaskThread', 'FTaskThread',
        'FOutputDevice', 'FDebug::', 'FPlatformStackWalk',
        'StaticFailDebug', 'FError::',
        'IPCThreadState::', 'BBinder::', 'BpBinder::',
        'ActivityThread.', 'Looper.', 'Handler.',
        'RuntimeInit$', 'ZygoteInit.',
        'art::Runtime::Abort', 'art::LogMessage',
    )

    funcs = set()
    for line in stack.split('\n'):
        line = line.strip()
        if not line:
            continue
        # C++ Class::method(
        m = re.search(r'(\w+(?:<[^>]*>)?)::(\w+)\s*\(', line)
        if m and len(m.group(2)) > 3:
            fn = f'{m.group(1)}::{m.group(2)}'
            if fn.lower() not in _GENERIC and not any(fn.startswith(p) for p in _GENERIC_PREFIXES):
                funcs.add(fn)
            continue
        # Java
        m = re.search(r'([\w$]+)\.([\w$]+)\(', line)
        if m and len(m.group(2)) > 3:
            funcs.add(f'{m.group(1)}.{m.group(2)}')

    return funcs


# ==================== 路线 C: LLM 仲裁 ====================

def _llm_compare(stack_a: str, stack_b: str, exc_a: str, exc_b: str, key_frame: str) -> tuple:
    """LLM 语义判断（仅在 A/B 路线冲突时调用）
    
    返回: (is_match: bool, reason: str)
    """
    # 截取关键帧附近
    stack_a_short = _truncate_around(stack_a, key_frame, 12)
    stack_b_short = _truncate_around(stack_b, key_frame, 12)

    # 读取历史反馈规则
    from ..memory import MemoryStore
    memory = MemoryStore()
    rules = memory.get_active_rules(category='history_compare')
    rules_text = ''
    if rules:
        rules_text = '\n## 从历史反馈中学到的经验（务必遵守）\n'
        for i, r in enumerate(rules[:5]):
            rules_text += f'{i+5}. {r["rule_text"]}\n'

    prompt = f"""你是崩溃分析专家。判断以下两个堆栈是否属于同一个 Bug。

## 关键帧
{key_frame}

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
1. 异常类型不同类（SIGSEGV vs SIGABRT）通常不是同一Bug
2. 重点看关键帧及其上下文调用链
3. 允许编译器优化导致的帧序微小变化
4. 忽略线程入口等通用帧差异
{rules_text}

回答 YES 或 NO，加一句理由。格式: YES/NO: 理由"""

    response = call_llm(prompt, temperature=0.1)
    if not response:
        return False, 'LLM不可用'

    is_match = response.strip().upper().startswith('YES')
    reason = response.strip().split(':', 1)[1].strip() if ':' in response else response.strip()
    return is_match, reason[:100]


def _truncate_around(stack: str, key_frame: str, context_lines: int = 12) -> str:
    """截取关键帧附近的堆栈"""
    lines = stack.split('\n')
    anchor_idx = -1
    for i, line in enumerate(lines):
        if key_frame in line:
            anchor_idx = i
            break
    if anchor_idx >= 0:
        start = max(0, anchor_idx - 5)
        end = min(len(lines), anchor_idx + context_lines + 1)
        selected = lines[start:end]
        if start > 0:
            selected.insert(0, f'... (省略前 {start} 行)')
        if end < len(lines):
            selected.append(f'... (省略后 {len(lines) - end} 行)')
        return '\n'.join(selected)
    if len(lines) > 25:
        return '\n'.join(lines[:25]) + f'\n... (省略后 {len(lines)-25} 行)'
    return stack
