"""Route 节点 — 三层渐进式意图识别 + 参数解析

Layer 1: 关键词快速匹配（0ms，零成本）
Layer 2: 历史案例匹配（从 episodic memory 中找相似 query）
Layer 3: LLM 兜底（仅前两层都无法确定时调用）
"""
import re
import json
import time
from datetime import datetime, timedelta
from ...llm_client import call_llm
from ...config import PROJECTS
from ...utils.project_resolver import resolve_project
from ...logging import get_logger
from ...utils.date_parser import parse_date_range


# ==================== Layer 1: 关键词规则匹配 ====================

# 意图关键词表（优先级从高到低）
INTENT_KEYWORDS = {
    'history_check': [
        (r'历史问题|正式服有没有|正式服有吗|是[老旧]问题|是新问题', 0.95),
        (r'top\s*\d.*正式|正式服.*top', 0.90),
    ],
    'trend_query': [
        (r'崩溃率[趋走]势|趋势|走势', 0.95),
        (r'崩溃率[多高低]|崩溃率怎么样', 0.90),
    ],
    'issue_detail': [
        (r'完整堆栈|堆栈详情|设备信息|详细看看|具体看看', 0.95),
        (r'top\s*\d.*堆栈|top\s*\d.*详情', 0.90),
    ],
    'compare': [
        (r'对比|比较|和.*[比对]|vs|变化', 0.90),
        (r'上周.*这周|这周.*上周|本周.*上周', 0.95),
    ],
    'crash_report': [
        (r'崩溃报告|崩溃分析|报告|分析', 0.85),
        (r'top\s*\d+|top\d+|崩溃情况|崩溃数据', 0.80),
        (r'(安卓|ios|鸿蒙|android|harmony).*(版本|昨天|今天|最近|一周)', 0.80),
    ],
}

# 日期关键词 → (start, end) 映射规则
DATE_PATTERNS = [
    (r'昨天', lambda t: ((t - timedelta(days=1)).strftime('%Y%m%d'), (t - timedelta(days=1)).strftime('%Y%m%d'))),
    (r'今天', lambda t: (t.strftime('%Y%m%d'), t.strftime('%Y%m%d'))),
    (r'最近[一1]周|这周|近7天|近七天', lambda t: ((t - timedelta(days=6)).strftime('%Y%m%d'), t.strftime('%Y%m%d'))),
    (r'最近3天|近3天|近三天', lambda t: ((t - timedelta(days=2)).strftime('%Y%m%d'), t.strftime('%Y%m%d'))),
    (r'最近30天|近30天|最近一个月', lambda t: ((t - timedelta(days=29)).strftime('%Y%m%d'), t.strftime('%Y%m%d'))),
    (r'上周', lambda t: (
        (t - timedelta(days=t.weekday() + 7)).strftime('%Y%m%d'),
        (t - timedelta(days=t.weekday() + 1)).strftime('%Y%m%d'),
    )),
]

# 版本号正则
VERSION_PATTERN = re.compile(r'(\d+\.\d+(?:\.\d+)*(?:\.\*)?)')


def _layer1_keyword_match(query: str, today: datetime) -> dict:
    """
    Layer 1: 纯规则匹配
    返回: {'matched': True/False, 'intent': ..., 'confidence': ..., 'params': {...}}
    """
    query_lower = query.lower()

    # 1. 意图匹配
    intent = None
    confidence = 0.0
    for intent_name, patterns in INTENT_KEYWORDS.items():
        for pattern, conf in patterns:
            if re.search(pattern, query_lower):
                if conf > confidence:
                    intent = intent_name
                    confidence = conf
                break  # 每个意图取第一个命中的 pattern

    if not intent:
        return {'matched': False}

    # 2. 项目匹配
    project_id = resolve_project(query)

    # 3. 日期匹配
    start_date, end_date = None, None
    for pattern, date_fn in DATE_PATTERNS:
        if re.search(pattern, query_lower):
            start_date, end_date = date_fn(today)
            break

    # ISO日期范围匹配: "2026-06-17~2026-06-23" 或 "2026/06/17~2026/06/23" 或 "2026-06-17-2026-06-23"
    if not start_date:
        m = re.search(r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})\s*[~\-至到]\s*(\d{4})[-/](\d{1,2})[-/](\d{1,2})', query)
        if m:
            start_date = f"{m.group(1)}{int(m.group(2)):02d}{int(m.group(3)):02d}"
            end_date = f"{m.group(4)}{int(m.group(5)):02d}{int(m.group(6)):02d}"

    # 单个ISO日期: "2026-06-17" 或 "2026/06/17"
    if not start_date:
        m = re.search(r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})', query)
        if m:
            start_date = f"{m.group(1)}{int(m.group(2)):02d}{int(m.group(3)):02d}"
            end_date = start_date

    # 精确日期匹配: "6月1号到今天" / "20号" 等
    if not start_date:
        m = re.search(r'(\d{1,2})[月.](\d{1,2})[号日]', query)
        if m:
            month, day = int(m.group(1)), int(m.group(2))
            year = today.year
            start_date = f"{year}{month:02d}{day:02d}"
            # 看是否有"到xxx"
            m2 = re.search(r'到.*?(?:今天|现在)', query)
            end_date = today.strftime('%Y%m%d') if m2 else start_date

    # 4. 版本匹配（支持逗号分隔的多版本）
    version = None
    # 先尝试匹配逗号分隔的多版本: "3.7.375.375.375,3.7.376.376.376"
    multi_vm = re.search(r'(\d+\.\d+(?:\.\d+)*(?:,\d+\.\d+(?:\.\d+)*)+)', query)
    if multi_vm:
        version = multi_vm.group(1)  # 保留逗号分隔格式
    else:
        vm = VERSION_PATTERN.search(query)
        if vm:
            ver = vm.group(1)
            # "3.7" → "3.7.*"
            if ver.count('.') == 1:
                version = ver + '.*'
            else:
                version = ver

    # 5. 判断参数是否完整
    missing = []
    if not project_id:
        missing.append('project_id')
    if intent == 'crash_report' and not version:
        missing.append('version')
    if intent in ('crash_report', 'trend_query', 'compare') and not start_date:
        missing.append('date_range')

    return {
        'matched': True,
        'intent': intent,
        'confidence': confidence,
        'project_id': project_id or None,
        'version': version,
        'start_date': start_date,
        'end_date': end_date,
        'missing_params': missing,
    }


# ==================== Layer 2: 历史案例匹配（SQLite 持久化）====================

from ...memory import MemoryStore

_memory = None  # 延迟初始化，避免 import 时连接数据库


def _get_memory() -> MemoryStore:
    """延迟初始化 MemoryStore 单例"""
    global _memory
    if _memory is None:
        _memory = MemoryStore()
    return _memory


def _layer2_episodic_match(query: str) -> dict:
    """
    Layer 2: 从 SQLite 持久化的历史案例中匹配
    用关键词搜索找相似 episode，按匹配度排序
    """
    similar = _get_memory().find_similar_episodes(query, limit=5)
    if not similar:
        return {'matched': False}

    # 取最相关的那条
    best = similar[0]

    # 计算关键词重叠度作为 confidence
    query_words = set(w for w in query.lower().split() if len(w) >= 2)
    case_words = set(w for w in best['query'].lower().split() if len(w) >= 2)
    if not query_words or not case_words:
        return {'matched': False}

    overlap = len(query_words & case_words) / max(len(query_words), len(case_words))

    if overlap >= 0.6:
        return {
            'matched': True,
            'intent': best['intent'],
            'confidence': min(overlap + 0.1, 0.90),
            'project_id': best.get('project_id'),
            'version': best.get('version'),
            'start_date': best.get('start_date'),
            'end_date': best.get('end_date'),
            'missing_params': [],
        }

    return {'matched': False}


def save_episodic_case(query: str, intent: str, project_id: str = None,
                       version: str = None, start_date: str = None, end_date: str = None):
    """保存成功案例到持久化 Memory"""
    _get_memory().save_episode(
        query=query, intent=intent, project_id=project_id,
        version=version, start_date=start_date, end_date=end_date,
        success=True,
    )


# ==================== Layer 3: LLM 兜底 ====================

def _layer3_llm_classify(query: str, history: list, today: datetime) -> dict:
    """Layer 3: 调用 LLM 做意图识别 + 参数解析（最贵，仅前两层失败时使用）"""
    today_str = today.strftime('%Y%m%d')
    yesterday_str = (today - timedelta(days=1)).strftime('%Y%m%d')

    history_context = ""
    if history:
        recent = history[-3:]
        history_context = "\n".join([
            f"用户: {t['user']}\n助手: {t['assistant'][:100]}..." for t in recent
        ])

    prompt = f"""分析用户查询，提取意图和参数。

当前日期: {today.strftime('%Y年%m月%d日')} (周{['一','二','三','四','五','六','日'][today.weekday()]})
昨天: {yesterday_str}

对话历史:
{history_context or '(首次对话)'}

用户说: "{query}"

项目: android_exp(安卓体验服) android_prod(安卓正式服) ios_exp ios_prod harmony_exp harmony_prod
（只说"安卓"默认体验服）

意图: crash_report / trend_query / issue_detail / history_check / compare / clarify
版本: "3.7"→"3.7.*", 不指定→"-1"
日期: "昨天"→{yesterday_str}, "最近一周"→{(today-timedelta(days=6)).strftime('%Y%m%d')}~{today_str}

输出JSON:
{{"intent":"...","confidence":0.0~1.0,"project_id":"...|null","version":"...|null","start_date":"YYYYMMDD|null","end_date":"YYYYMMDD|null","issue_id":"...|null","missing_params":["缺失参数"]}}"""

    response = call_llm(prompt, temperature=0.1)

    try:
        cleaned = response.strip()
        if cleaned.startswith('```'):
            cleaned = cleaned.split('\n', 1)[1]
            cleaned = cleaned.rsplit('```', 1)[0]
        result = json.loads(cleaned)
        return {
            'matched': True,
            'intent': result.get('intent', 'clarify'),
            'confidence': result.get('confidence', 0.5),
            'project_id': result.get('project_id'),
            'version': result.get('version'),
            'start_date': result.get('start_date'),
            'end_date': result.get('end_date'),
            'issue_id': result.get('issue_id'),
            'missing_params': result.get('missing_params', []),
        }
    except (json.JSONDecodeError, IndexError):
        return {
            'matched': False,
            'intent': 'clarify',
            'confidence': 0.0,
            'missing_params': ['LLM解析失败'],
        }


# ==================== 多意图提取 ====================

# 意图执行优先级（数字小的先执行）
INTENT_PRIORITY = {
    'crash_report': 1,
    'trend_query': 2,
    'compare': 2,
    'history_check': 3,   # 依赖 crash_report 的 Top1
    'issue_detail': 3,    # 同上
    'clarify': 99,
}

MAX_INTENTS = 3  # 单次最多处理的意图数


def _extract_multi_intents(query: str, today: datetime) -> list:
    """从 query 中提取所有可能的意图（不止最高分的那一个）
    
    返回: [{'intent': ..., 'confidence': ...}, ...]
    """
    query_lower = query.lower()
    matched = []
    seen_intents = set()

    for intent_name, patterns in INTENT_KEYWORDS.items():
        for pattern, conf in patterns:
            if re.search(pattern, query_lower):
                if intent_name not in seen_intents:
                    matched.append({'intent': intent_name, 'confidence': conf})
                    seen_intents.add(intent_name)
                break

    # 按置信度降序
    matched.sort(key=lambda x: -x['confidence'])
    return matched


def _filter_and_sort_intents(intents: list) -> tuple:
    """过滤无效意图 + 按依赖排序 + 限制数量
    
    返回: (active_intents, deferred_intents)
    - active_intents: 本次执行的（最多 MAX_INTENTS 个）
    - deferred_intents: 被推迟的（超出上限的）
    """
    # 过滤：去掉 clarify 和低置信度
    valid = [i for i in intents if i['intent'] != 'clarify' and i['confidence'] >= 0.7]

    # 去重
    seen = set()
    unique = []
    for item in valid:
        if item['intent'] not in seen:
            seen.add(item['intent'])
            unique.append(item)

    # 按依赖优先级排序（先执行的排前面）
    unique.sort(key=lambda x: INTENT_PRIORITY.get(x['intent'], 10))

    # 拆分：前 MAX_INTENTS 个执行，剩余推迟
    active = unique[:MAX_INTENTS]
    deferred = unique[MAX_INTENTS:]

    return active, deferred


# ==================== Route 节点主函数 ====================

def route_node(state: dict) -> dict:
    """
    三层渐进式意图路由（支持多意图）:
    
    1. 提取所有匹配的意图
    2. 过滤无效的 + 按依赖排序 + 限制数量
    3. 超出上限的放入 deferred_intents，在回答末尾提示用户
    4. 参数解析仍用最高置信度的结果
    
    Layer 1: 关键词规则匹配（0ms, 零成本）
       ↓ 未命中或置信度 < 0.8
    Layer 2: 历史案例匹配（内存查找, 几乎零成本）
       ↓ 未命中
    Layer 3: LLM 分类（~1s, 消耗 token）
    """
    query = state['query']
    history = state.get('session_history', [])
    today = datetime.now()
    logger = get_logger()
    start_time = time.time()

    # ─── 多意图提取（Layer 1 级别） ───
    multi_intents = _extract_multi_intents(query, today)
    active_intents, deferred_intents = _filter_and_sort_intents(multi_intents)

    if len(active_intents) > 1:
        logger._write('info', 'route', 'multi_intent', {
            'active': [i['intent'] for i in active_intents],
            'deferred': [i['intent'] for i in deferred_intents],
        })

    # ─── Layer 1: 关键词快速匹配（取主意图的参数）───
    l1_result = _layer1_keyword_match(query, today)
    if l1_result.get('matched') and l1_result.get('confidence', 0) >= 0.8:
        duration = int((time.time() - start_time) * 1000)
        primary_intent = active_intents[0]['intent'] if active_intents else l1_result['intent']
        logger.log_route(query, primary_intent, l1_result['confidence'], 'layer1',
                         l1_result.get('project_id'), l1_result.get('version'),
                         l1_result.get('start_date'), l1_result.get('end_date'), duration_ms=duration)
        save_episodic_case(query, primary_intent, l1_result.get('project_id'),
                          l1_result.get('version'), l1_result.get('start_date'), l1_result.get('end_date'))
        return {
            'intent': primary_intent,
            'confidence': l1_result['confidence'],
            'intents': active_intents if len(active_intents) > 1 else [],
            'deferred_intents': deferred_intents,
            'project_id': l1_result.get('project_id'),
            'version': l1_result.get('version'),
            'start_date': l1_result.get('start_date'),
            'end_date': l1_result.get('end_date'),
            'issue_id': l1_result.get('issue_id'),
            'missing_params': l1_result.get('missing_params', []),
        }

    # ─── Layer 2: 历史案例匹配 ───
    l2_result = _layer2_episodic_match(query)
    if l2_result.get('matched'):
        duration = int((time.time() - start_time) * 1000)
        logger.log_route(query, l2_result['intent'], l2_result['confidence'], 'layer2',
                         l2_result.get('project_id'), l2_result.get('version'),
                         l2_result.get('start_date'), l2_result.get('end_date'), duration_ms=duration)
        return {
            'intent': l2_result['intent'],
            'confidence': l2_result['confidence'],
            'intents': active_intents if len(active_intents) > 1 else [],
            'deferred_intents': deferred_intents,
            'project_id': l2_result.get('project_id'),
            'version': l2_result.get('version'),
            'start_date': l2_result.get('start_date'),
            'end_date': l2_result.get('end_date'),
            'issue_id': l2_result.get('issue_id'),
            'missing_params': l2_result.get('missing_params', []),
        }

    # ─── Layer 3: LLM 兜底 ───
    l3_result = _layer3_llm_classify(query, history, today)

    if l3_result.get('matched'):
        duration = int((time.time() - start_time) * 1000)
        logger.log_route(query, l3_result['intent'], l3_result['confidence'], 'layer3',
                         l3_result.get('project_id'), l3_result.get('version'),
                         l3_result.get('start_date'), l3_result.get('end_date'), duration_ms=duration)
        save_episodic_case(query, l3_result['intent'], l3_result.get('project_id'),
                          l3_result.get('version'), l3_result.get('start_date'), l3_result.get('end_date'))
        return {
            'intent': l3_result['intent'],
            'confidence': l3_result['confidence'],
            'intents': active_intents if len(active_intents) > 1 else [],
            'deferred_intents': deferred_intents,
            'project_id': l3_result.get('project_id'),
            'version': l3_result.get('version'),
            'start_date': l3_result.get('start_date'),
            'end_date': l3_result.get('end_date'),
            'issue_id': l3_result.get('issue_id'),
            'missing_params': l3_result.get('missing_params', []),
        }

    # 全部失败
    logger._write('warn', 'route', 'all_layers_missed', {'query': query[:60]})
    return {
        'intent': 'clarify',
        'confidence': 0.0,
        'intents': [],
        'deferred_intents': [],
        'missing_params': ['无法理解用户意图'],
    }
