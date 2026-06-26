"""历史问题判定工具

搜索流程保持原样（关键帧提取 + 多帧搜索 + 拉候选堆栈）
仅最终的匹配判断从手写算法替换为 LLM 语义对比
"""
import time
import logging
from ..config import PROJECTS, CRASHSIGHT_BASE
from ..api_client import openapi_post
from ..llm_client import call_llm
from .keyframe import extract_key_frame

logger = logging.getLogger(__name__)


def execute(project_id: str, issue_id: str, exp_stack: str, exp_exception: str = '') -> dict:
    """
    判断崩溃问题是否在正式服/历史版本存在
    
    流程（与原 app.py 一致）:
    1. extract_key_frame() → 四轮打分提取关键帧 + 特征组(top3)
    2. _multi_search_candidates() → 用 feature_frames 分别搜 advancedSearchEx，合并去重
    3. 逐个候选拉堆栈
    4. LLM 对比判断是否同一 Bug（替代原来的 _hard_anchor_match + Jaccard）
    """
    project = PROJECTS.get(project_id)
    if not project:
        raise ValueError(f"项目不存在: {project_id}")

    # 确定搜索目标
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

    # ── Step 1: 提取关键帧（原版四轮算法）──
    kf_ret = extract_key_frame(exp_stack, exp_stack, exception_name=exp_exception)
    if not kf_ret:
        return {'isHistory': False, 'reason': '无法从堆栈提取关键帧'}

    key_frame, is_weak, feature_frames = kf_ret
    logger.info(f'关键帧="{key_frame}" 特征组={feature_frames}')

    # ── Step 2: 多帧搜索候选（原版逻辑）──
    candidates = _multi_search_candidates(target_app_id, target_platform_id, feature_frames, limit_per_kw=10)

    # 排除自己
    candidates = [c for c in candidates
                  if not (c.get('issueId', '').startswith(issue_id[:8]) or
                          issue_id.startswith(c.get('issueId', '')[:8]))]

    if not candidates:
        return {'isHistory': False, 'reason': f'正式服未搜到包含 "{key_frame}" 的问题'}

    logger.info(f'搜到 {len(candidates)} 个候选')

    # ── Step 3 & 4: 逐个候选拉堆栈 + LLM 判断 ──
    # 策略: Jaccard 做快速排除（明显不相关的不浪费 LLM），最终判定全靠 LLM
    from .ensemble import _jaccard_match, _exception_compatible

    for i, cand in enumerate(candidates[:5]):
        cand_id = cand.get('issueId', '')
        cand_exception = cand.get('exceptionName', '')

        # 拉堆栈
        cand_stack = _get_candidate_stack(cand, target_app_id, target_platform_id)
        if not cand_stack:
            print(f'[History]   候选 {cand_id[:8]} 堆栈为空，跳过')
            continue

        # 快速排除 1: 异常类型不兼容（如 SIGSEGV vs SIGABRT）
        if exp_exception and cand_exception and not _exception_compatible(exp_exception, cand_exception):
            logger.debug(f'候选 {cand_id[:8]} 异常类型不兼容({exp_exception} vs {cand_exception})，跳过')
            continue

        # 快速排除 2: Jaccard 极低（<0.3）说明两个堆栈完全无关
        _, jaccard_score = _jaccard_match(exp_stack, cand_stack, threshold=0.3)
        if jaccard_score < 0.3:
            logger.debug(f'候选 {cand_id[:8]} Jaccard={jaccard_score:.2f} 太低，跳过')
            continue

        # LLM 判断（最终裁决）
        logger.info(f'候选 {cand_id[:8]} ({cand_exception}) Jaccard={jaccard_score:.2f}，LLM 判断中...')
        is_same = _llm_compare_stacks(exp_stack, cand_stack, exp_exception, cand_exception, key_frame)

        if is_same:
            prod_url = f'https://crashsight.qq.com/crash-reporting/crashes/{target_app_id}/{cand_id}?pid={target_platform_id}'
            logger.info(f'LLM 判定匹配: {cand_id[:8]}')
            return {
                'isHistory': True,
                'prodIssueId': cand_id,
                'prodException': cand_exception,
                'prodUrl': prod_url,
                'prodCrashCount': cand.get('crashNum') or cand.get('count') or 0,
                'prodAffectedUsers': cand.get('imeiCount', 0),
                'matchedKeyFrame': key_frame,
                'jaccard': jaccard_score,
            }

        logger.debug(f'LLM 判定不匹配: {cand_id[:8]}')
        time.sleep(1)

    return {'isHistory': False, 'reason': '候选堆栈均不匹配', 'keyFrame': key_frame}


# ==================== 多帧搜索（原版逻辑）====================

def _multi_search_candidates(app_id: str, platform_id: int, feature_frames: list, limit_per_kw: int = 10) -> list:
    """用多个关键帧分别搜索 advancedSearchEx，合并去重候选列表（与原 app.py 一致）"""
    if not feature_frames:
        return []

    all_candidates = {}
    seen_ids = set()

    for kf_text in feature_frames:
        search_text = kf_text.strip('[]')
        if not search_text:
            continue

        conditions = [
            {'queryType': 'TERMS_WILDCARD', 'field': 'version', 'terms': ['1.*']},
            {'field': 'exceptionType'},
            {'queryType': 'TEXT_MATCH_PHRASE', 'field': 'crashDetail', 'text': search_text, 'not': False},
        ]

        body = {
            'appId': app_id,
            'platformId': int(platform_id),
            'pid': str(platform_id),
            'desc': 'true',
            'rows': limit_per_kw,
            'start': '0',
            'sortField': 'matchCount',
            'searchConditionGroup': {'conditions': conditions},
            'enableSearchOomLinkAdvancedSearch': True,
            'oomDesc': True,
            'oomNum': 10,
            'oomNumOrder': 10,
            'oomSortField': 'uploadTimestamp',
            'oomStart': 0,
        }

        try:
            data = openapi_post(f'{CRASHSIGHT_BASE}/uniform/openapi/advancedSearchEx', body, timeout=15)
            inner = data.get('data', {}) if isinstance(data.get('data'), dict) else {}
            if not inner:
                inner = data.get('ret', {}) if isinstance(data.get('ret'), dict) else {}
            issue_list = inner.get('issueList', []) or []
            num_found = inner.get('numFound', 0)
            logger.debug(f'搜索 "{kf_text[:40]}" → {num_found} 条')

            for iss in issue_list:
                iid = iss.get('issueId', '')
                if iid and iid not in seen_ids:
                    seen_ids.add(iid)
                    all_candidates[iid] = iss
        except Exception as e:
            logger.warning(f'搜索 "{kf_text[:40]}" 失败: {e}')
            continue

        time.sleep(0.5)

    candidates = list(all_candidates.values())
    candidates.sort(key=lambda x: x.get('matchCount', 0) or 0, reverse=True)
    return candidates


# ==================== 候选堆栈获取 ====================

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
        logger.warning(f'获取候选 {cand_id[:8]} 堆栈失败: {e}')
        return ''


# ==================== LLM 对比判断（替代 _hard_anchor_match + Jaccard）====================

# 规则缓存（避免每次都查数据库）
_rules_cache = {'rules': [], 'last_refresh': 0}
_RULES_CACHE_TTL = 60  # 缓存 60 秒


def _get_relevant_rules(key_frame: str, exc_a: str, stack_a: str) -> list:
    """获取与当前堆栈相关的规则（智能匹配，限制数量）
    
    策略：
    1. 从 DB 读所有活跃规则（带缓存）
    2. 按相关性打分：规则文本中包含当前堆栈的关键词则加分
    3. 取 top 5 条最相关的
    4. 如果没有相关规则，不注入（避免无关规则干扰 LLM）
    """
    import time as _time

    # 带缓存读取
    now = _time.time()
    if now - _rules_cache['last_refresh'] > _RULES_CACHE_TTL:
        _rules_cache['rules'] = _memory.get_active_rules(category='history_compare')
        _rules_cache['last_refresh'] = now

    all_rules = _rules_cache['rules']
    if not all_rules:
        return []

    # 提取当前上下文的关键词（用于匹配规则相关性）
    context_keywords = set()
    # 从关键帧提取
    if key_frame:
        for part in key_frame.replace('::', ' ').replace('(', ' ').replace(')', ' ').split():
            if len(part) >= 3:
                context_keywords.add(part.lower())
    # 从异常类型提取
    if exc_a:
        context_keywords.add(exc_a.lower().split('(')[0])  # SIGSEGV
    # 从堆栈提取模块名
    for line in stack_a.split('\n')[:10]:
        if '.so' in line:
            import re
            so_match = re.search(r'(lib\w+\.so)', line)
            if so_match:
                context_keywords.add(so_match.group(1).lower())
        if 'art::gc' in line.lower():
            context_keywords.add('gc')
            context_keywords.add('art')

    # 给每条规则打相关性分
    scored_rules = []
    for rule in all_rules:
        rule_text = rule.get('rule_text', '').lower()
        score = rule.get('confidence', 0.7)  # 基础分=confidence

        # 关键词命中加分
        hits = sum(1 for kw in context_keywords if kw in rule_text)
        score += hits * 0.2

        # 完全无关的规则不要（0命中且不是通用规则）
        is_general = any(w in rule_text for w in ['当', '应', '如果', '注意'])
        if hits == 0 and not is_general:
            continue

        scored_rules.append((score, rule['rule_text']))

    # 按分数排序，取 top 5
    scored_rules.sort(key=lambda x: -x[0])
    return [text for _, text in scored_rules[:5]]


def _llm_compare_stacks(stack_a: str, stack_b: str, exc_a: str, exc_b: str, key_frame: str) -> bool:
    """用 LLM 判断两个崩溃堆栈是否为同一 Bug
    
    增强：注入从用户反馈中学到的相关规则（智能匹配 + 限数量）
    """
    # 截断堆栈避免 token 过长（保留关键帧附近）
    stack_a_short = _truncate_around_keyframe(stack_a, key_frame, context_lines=12)
    stack_b_short = _truncate_around_keyframe(stack_b, key_frame, context_lines=12)

    # 获取相关规则（最多 5 条，按相关性排序）
    learned_rules = _get_relevant_rules(key_frame, exc_a, stack_a)
    rules_section = ''
    if learned_rules:
        rules_text = '\n'.join([f'- {r}' for r in learned_rules])
        rules_section = f"""
## 历史经验（从过往纠错中学到的，请重点参考）
{rules_text}
"""

    prompt = f"""你是一个崩溃分析专家。请判断以下两个崩溃堆栈是否属于同一个 Bug（同一根因）。

## 关键帧（已通过算法提取的最重要函数）
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
1. 如果异常类型完全不同类（如 SIGSEGV vs SIGABRT），通常不是同一 Bug。但 SIGSEGV 和 SIGBUS 都属于内存访问错误，可以是同一 Bug。
2. 重点关注关键帧及其上下文调用链是否一致
3. 允许编译器优化导致的帧序微小变化（如中间多/少几帧框架函数）
4. 忽略线程入口、系统框架等通用帧的差异
5. 核心判断标准：崩溃的根因函数 + 上下文调用路径是否一致
{rules_section}
请只回答 YES 或 NO，然后用一句话说明理由。
格式: YES/NO: 理由"""

    response = call_llm(prompt, temperature=0.1)
    if not response:
        logger.warning('LLM 不可用，默认判为不匹配')
        return False

    answer = response.strip().upper()
    is_same = answer.startswith('YES')
    reason = response.strip().split(':', 1)[1].strip() if ':' in response else response.strip()
    rules_count = len(learned_rules)
    logger.info(f'LLM 判定: {"Match" if is_same else "Mismatch"} | {reason[:60]} (注入{rules_count}条规则)')
    return is_same


def _truncate_around_keyframe(stack: str, key_frame: str, context_lines: int = 12) -> str:
    """截取关键帧附近的堆栈（上下各 context_lines 行）"""
    lines = stack.split('\n')

    # 找到关键帧所在行
    anchor_idx = -1
    for i, line in enumerate(lines):
        if key_frame in line:
            anchor_idx = i
            break

    if anchor_idx >= 0:
        # 取关键帧上方 5 行 + 下方 context_lines 行
        start = max(0, anchor_idx - 5)
        end = min(len(lines), anchor_idx + context_lines + 1)
        selected = lines[start:end]
        if start > 0:
            selected.insert(0, f'... (省略前 {start} 行)')
        if end < len(lines):
            selected.append(f'... (省略后 {len(lines) - end} 行)')
        return '\n'.join(selected)
    else:
        # 关键帧不在堆栈里，取前 25 行
        if len(lines) > 25:
            return '\n'.join(lines[:25]) + f'\n... (省略后 {len(lines) - 25} 行)'
        return stack
