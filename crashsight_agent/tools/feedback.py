"""自进化闭环 — 用户反馈 → 追问原因 → LLM 提炼规则 → 存入 Memory → 下次注入 prompt

流程:
1. 用户说"判错了" → 记录错误案例
2. Agent 追问原因 → 用户回答
3. LLM 基于用户反馈提炼规则
4. 规则存入 semantic_rule 表
5. 下次 _llm_compare 时自动从 DB 读规则注入 prompt
"""
import os
import json
import sqlite3
from datetime import datetime
from ..llm_client import call_llm
from ..memory import MemoryStore


# 反馈数据库（和 memory 共用一个 SQLite）
_memory = MemoryStore()

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'data', 'memory.sqlite')


def _ensure_feedback_table():
    """确保 feedback 表存在"""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            issue_id TEXT,
            key_frame TEXT,
            exp_stack_head TEXT,
            prod_stack_head TEXT,
            original_prediction TEXT,
            ground_truth TEXT,
            user_reason TEXT,
            generated_rule TEXT,
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)
    conn.commit()
    conn.close()


_ensure_feedback_table()


# ==================== 追问选项 ====================

FEEDBACK_OPTIONS = [
    {'key': 'A', 'text': '异常类型/信号不同（一个是崩溃一个是卡死）'},
    {'key': 'B', 'text': '关键帧相同但调用链/上下文不同'},
    {'key': 'C', 'text': '是同名函数但不同模块/线程'},
    {'key': 'D', 'text': '已修复的老问题，这次是新引入的'},
    {'key': 'E', 'text': '其他原因（请补充说明）'},
]


def get_clarify_question() -> str:
    """生成反馈追问文本"""
    lines = ["收到反馈。请问为什么不是同一个 Bug？\n"]
    for opt in FEEDBACK_OPTIONS:
        lines.append(f"  {opt['key']}. {opt['text']}")
    lines.append("\n请回复选项字母，或直接说明原因。")
    return '\n'.join(lines)


# ==================== 记录反馈 + 提炼规则 ====================

def record_feedback(
    issue_id: str,
    key_frame: str,
    exp_stack: str,
    prod_stack: str,
    original_prediction: str,
    ground_truth: str,
    user_reason: str,
) -> dict:
    """
    记录用户反馈 + 提炼规则
    
    返回: {'success': True, 'rule': '提炼出的规则文本'}
    """
    # 1. 存反馈记录
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """INSERT INTO feedback (issue_id, key_frame, exp_stack_head, prod_stack_head,
           original_prediction, ground_truth, user_reason)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (issue_id, key_frame,
         '\n'.join(exp_stack.split('\n')[:15]),
         '\n'.join(prod_stack.split('\n')[:15]),
         original_prediction, ground_truth, user_reason)
    )
    conn.commit()
    conn.close()

    # 2. LLM 提炼规则
    rule_text = _extract_rule(key_frame, user_reason, exp_stack, prod_stack)

    if rule_text:
        # 3. 存入 semantic_rule（confidence=0.9 因为有用户确认）
        _memory.add_rule(
            rule_text=rule_text,
            category='history_compare',
            confidence=0.9,
            source_episodes=f'feedback_{issue_id}_{datetime.now().strftime("%Y%m%d%H%M")}',
        )
        
        # 更新 feedback 记录（取最新一条的 id 来更新，兼容标准 SQLite）
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute(
            "SELECT id FROM feedback WHERE issue_id=? ORDER BY id DESC LIMIT 1",
            (issue_id,)
        ).fetchone()
        if row:
            conn.execute("UPDATE feedback SET generated_rule=? WHERE id=?", (rule_text, row[0]))
        conn.commit()
        conn.close()

        return {'success': True, 'rule': rule_text}

    return {'success': True, 'rule': None, 'message': '已记录反馈，但未能提炼出规则'}


def _extract_rule(key_frame: str, user_reason: str, exp_stack: str, prod_stack: str) -> str:
    """用 LLM 从用户反馈中提炼判断规则"""
    # 展开选项字母为完整描述
    reason_expanded = user_reason
    for opt in FEEDBACK_OPTIONS:
        if user_reason.strip().upper() == opt['key']:
            reason_expanded = opt['text']
            break

    prompt = f"""用户反馈说以下两个崩溃堆栈不是同一个 Bug。

关键帧: {key_frame}
用户给的原因: {reason_expanded}

体验服堆栈(前10行):
{chr(10).join(exp_stack.split(chr(10))[:10])}

正式服堆栈(前10行):
{chr(10).join(prod_stack.split(chr(10))[:10])}

请根据用户的反馈，生成一条简短的判断规则（一句话），以后遇到类似情况时应该怎么判断。

要求:
- 规则要具体可执行，不要太泛化
- 用"当...时，应..."的句式
- 不超过 50 字

直接输出规则，不要加任何前缀。"""

    rule = call_llm(prompt, temperature=0.2)
    if rule:
        rule = rule.strip().strip('"').strip()
        if len(rule) > 10:  # 太短的规则没意义
            return rule
    return ''


# ==================== 查询历史反馈 ====================

def get_feedback_stats() -> dict:
    """获取反馈统计"""
    conn = sqlite3.connect(DB_PATH)
    total = conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]
    with_rule = conn.execute("SELECT COUNT(*) FROM feedback WHERE generated_rule IS NOT NULL AND generated_rule != ''").fetchone()[0]
    conn.close()

    rules = _memory.get_active_rules(category='history_compare')

    return {
        'total_feedback': total,
        'rules_generated': with_rule,
        'active_rules': len(rules),
        'rules': [r['rule_text'] for r in rules[:10]],
    }
