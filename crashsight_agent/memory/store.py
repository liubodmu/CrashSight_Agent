"""三层记忆系统 — Episodic + Skill + Semantic Rule

Episodic Memory: 每次查询的完整记录（query → intent → 参数 → 结果 → 成败）
Skill Memory:    从多次成功中提炼的可复用模式（"安卓体验服+版本+昨天" → crash_report）
Semantic Rule:   从失败中提炼的语义规则（"只说版本号不说项目时，默认安卓体验服"）
"""
import os
import json
import time
import sqlite3
from datetime import datetime


DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'data', 'memory.sqlite')


class MemoryStore:
    """三层记忆系统（SQLite 持久化）"""

    def __init__(self, db_path: str = None):
        self.db_path = db_path or DB_PATH
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

    def _init_db(self):
        """建表"""
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS episodic (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query TEXT NOT NULL,
                intent TEXT,
                project_id TEXT,
                version TEXT,
                start_date TEXT,
                end_date TEXT,
                success INTEGER DEFAULT 1,
                answer_summary TEXT,
                created_at TEXT DEFAULT (datetime('now', 'localtime')),
                importance REAL DEFAULT 0.5
            );

            CREATE TABLE IF NOT EXISTS skill (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pattern TEXT NOT NULL,
                intent TEXT NOT NULL,
                project_id TEXT,
                version TEXT,
                confidence REAL DEFAULT 0.8,
                use_count INTEGER DEFAULT 0,
                success_count INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now', 'localtime')),
                updated_at TEXT DEFAULT (datetime('now', 'localtime'))
            );

            CREATE TABLE IF NOT EXISTS semantic_rule (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rule_text TEXT NOT NULL,
                category TEXT,
                confidence REAL DEFAULT 0.7,
                source_episodes TEXT,
                created_at TEXT DEFAULT (datetime('now', 'localtime')),
                active INTEGER DEFAULT 1
            );

            CREATE INDEX IF NOT EXISTS idx_episodic_query ON episodic(query);
            CREATE INDEX IF NOT EXISTS idx_episodic_intent ON episodic(intent);
            CREATE INDEX IF NOT EXISTS idx_skill_pattern ON skill(pattern);
            CREATE INDEX IF NOT EXISTS idx_skill_confidence ON skill(confidence DESC);
        """)
        conn.close()

    # ==================== Episodic Memory ====================

    def save_episode(self, query: str, intent: str, project_id: str = None,
                     version: str = None, start_date: str = None, end_date: str = None,
                     success: bool = True, answer_summary: str = ''):
        """保存一次查询记录"""
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """INSERT INTO episodic (query, intent, project_id, version, start_date, end_date, success, answer_summary)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (query, intent, project_id, version, start_date, end_date, int(success), answer_summary[:200])
        )
        conn.commit()
        conn.close()

        # 自动触发 Skill 提炼
        self._try_extract_skill(query, intent, project_id, version)

    def find_similar_episodes(self, query: str, limit: int = 5) -> list:
        """查找相似的历史查询（多策略匹配 + 相似度打分）
        
        策略：
        1. 中文分词 + 同义词展开
        2. 多关键词 LIKE 搜索（召回）
        3. 对召回结果用 token 重叠度打分（排序）
        4. 按分数降序返回 top N
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row

        # 分词：按空格/标点拆分 + 单字过滤 + 同义词展开
        raw_words = _tokenize(query)
        if not raw_words:
            conn.close()
            return []

        # 同义词展开（增加召回率）
        expanded = set(raw_words)
        for w in raw_words:
            synonyms = _get_synonyms(w)
            expanded.update(synonyms)

        # 用所有词做 LIKE 搜索（宽召回）
        search_words = list(expanded)
        conditions = ' OR '.join(['query LIKE ?' for _ in search_words])
        params = [f'%{kw}%' for kw in search_words]

        # 多召回一些，后面打分排序
        rows = conn.execute(
            f"""SELECT * FROM episodic WHERE success=1 AND ({conditions})
                ORDER BY created_at DESC LIMIT ?""",
            params + [limit * 5]
        ).fetchall()
        conn.close()

        if not rows:
            return []

        # 对召回结果打分（token 重叠度 + 意图匹配加分）
        query_tokens = set(raw_words)
        scored = []
        for row in rows:
            row_dict = dict(row)
            row_tokens = set(_tokenize(row_dict.get('query', '')))
            
            if not row_tokens:
                continue

            # Jaccard 相似度
            intersection = query_tokens & row_tokens
            union = query_tokens | row_tokens
            jaccard = len(intersection) / len(union) if union else 0

            # 关键词命中加权（版本号/项目名命中权重更高）
            weighted_score = jaccard
            for token in intersection:
                if _is_version(token):
                    weighted_score += 0.15
                elif _is_project_keyword(token):
                    weighted_score += 0.10

            row_dict['_score'] = weighted_score
            scored.append(row_dict)

        # 按分数排序，返回 top N
        scored.sort(key=lambda x: x['_score'], reverse=True)
        
        # 过滤低分（< 0.2 的不要）
        results = [r for r in scored[:limit] if r['_score'] >= 0.2]
        
        # 移除内部评分字段
        for r in results:
            r.pop('_score', None)

        return results

    def get_recent_episodes(self, limit: int = 10) -> list:
        """获取最近 N 条记录"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM episodic ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        conn.close()
        return [dict(row) for row in rows]

    # ==================== Skill Memory ====================

    def _try_extract_skill(self, query: str, intent: str, project_id: str, version: str):
        """
        自动提炼 Skill:
        如果同一 intent+project 组合出现 >= 3 次，提炼为 Skill
        """
        conn = sqlite3.connect(self.db_path)
        count = conn.execute(
            "SELECT COUNT(*) FROM episodic WHERE intent=? AND project_id=? AND success=1",
            (intent, project_id)
        ).fetchone()[0]

        if count >= 3:
            # 检查是否已有此 skill
            existing = conn.execute(
                "SELECT id FROM skill WHERE intent=? AND project_id=?",
                (intent, project_id)
            ).fetchone()

            if existing:
                # 更新使用次数
                conn.execute(
                    "UPDATE skill SET use_count=use_count+1, success_count=success_count+1, updated_at=datetime('now','localtime') WHERE id=?",
                    (existing[0],)
                )
            else:
                # 新建 skill
                pattern = f"{intent}_{project_id}"
                conn.execute(
                    "INSERT INTO skill (pattern, intent, project_id, version, confidence) VALUES (?,?,?,?,?)",
                    (pattern, intent, project_id, version, 0.85)
                )
                print(f'[Memory] 新 Skill 提炼: {pattern} (出现{count}次)')

        conn.commit()
        conn.close()

    def find_skill(self, intent: str = None, project_id: str = None) -> dict:
        """查找匹配的 Skill"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row

        conditions = []
        params = []
        if intent:
            conditions.append("intent=?")
            params.append(intent)
        if project_id:
            conditions.append("project_id=?")
            params.append(project_id)

        where = " AND ".join(conditions) if conditions else "1=1"
        row = conn.execute(
            f"SELECT * FROM skill WHERE {where} AND confidence>=0.7 ORDER BY confidence DESC, use_count DESC LIMIT 1",
            params
        ).fetchone()
        conn.close()

        return dict(row) if row else None

    def get_all_skills(self) -> list:
        """获取所有 Skill"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM skill ORDER BY use_count DESC").fetchall()
        conn.close()
        return [dict(row) for row in rows]

    # ==================== Semantic Rule ====================

    def add_rule(self, rule_text: str, category: str = 'general', confidence: float = 0.7,
                 source_episodes: str = ''):
        """添加语义规则"""
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT INTO semantic_rule (rule_text, category, confidence, source_episodes) VALUES (?,?,?,?)",
            (rule_text, category, confidence, source_episodes)
        )
        conn.commit()
        conn.close()
        print(f'[Memory] 新规则: [{category}] {rule_text}')

    def get_active_rules(self, category: str = None) -> list:
        """获取生效中的规则（confidence >= 0.7）"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row

        if category:
            rows = conn.execute(
                "SELECT * FROM semantic_rule WHERE active=1 AND confidence>=0.7 AND category=? ORDER BY confidence DESC",
                (category,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM semantic_rule WHERE active=1 AND confidence>=0.7 ORDER BY confidence DESC"
            ).fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def decay_rules(self, decay_rate: float = 0.05):
        """规则衰减（定期调用，降低老规则置信度）"""
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "UPDATE semantic_rule SET confidence = confidence - ? WHERE active=1 AND confidence > 0.3",
            (decay_rate,)
        )
        # 置信度低于 0.3 的自动失活
        conn.execute("UPDATE semantic_rule SET active=0 WHERE confidence < 0.3")
        conn.commit()
        conn.close()

    # ==================== 统计 ====================

    def get_episode_count(self) -> int:
        """获取 episodic 表总条数"""
        conn = sqlite3.connect(self.db_path)
        count = conn.execute("SELECT COUNT(*) FROM episodic").fetchone()[0]
        conn.close()
        return count

    def get_stats(self) -> dict:
        """获取记忆系统统计"""
        conn = sqlite3.connect(self.db_path)
        episodic_count = conn.execute("SELECT COUNT(*) FROM episodic").fetchone()[0]
        skill_count = conn.execute("SELECT COUNT(*) FROM skill").fetchone()[0]
        rule_count = conn.execute("SELECT COUNT(*) FROM semantic_rule WHERE active=1").fetchone()[0]
        success_rate = conn.execute(
            "SELECT ROUND(AVG(success)*100, 1) FROM episodic"
        ).fetchone()[0] or 0
        conn.close()

        return {
            'episodic_count': episodic_count,
            'skill_count': skill_count,
            'rule_count': rule_count,
            'success_rate': f"{success_rate}%",
        }


# ==================== 分词与同义词工具函数 ====================

import re as _re

# 同义词表（领域相关）
_SYNONYMS = {
    '安卓': ['android', '安卓体验服', '安卓正式服'],
    'android': ['安卓', '安卓体验服'],
    'ios': ['苹果', 'iphone', 'iOS体验服'],
    '苹果': ['ios', 'iOS'],
    '鸿蒙': ['harmony', '鸿蒙体验', '鸿蒙正式'],
    'harmony': ['鸿蒙'],
    '崩溃': ['crash', '闪退', '崩溃率'],
    '趋势': ['走势', 'trend', '变化'],
    '走势': ['趋势', '变化'],
    '昨天': ['yesterday', '昨日'],
    '今天': ['today', '今日'],
    '最近一周': ['近7天', '这周', '近一周'],
    '历史问题': ['老问题', '旧问题', '正式服有'],
    '新问题': ['新增', '新引入'],
    '体验服': ['exp', '体验版'],
    '正式服': ['prod', '正式版', '线上'],
}


def _tokenize(text: str) -> list:
    """中文分词（简单但有效）
    
    策略：
    1. 按空格/标点拆分
    2. 对长中文串做 bigram 切分
    3. 保留版本号完整
    4. 过滤单字和停用词
    """
    if not text:
        return []

    # 先提取版本号（保持完整）
    versions = _re.findall(r'\d+\.\d+(?:\.\d+)*', text)
    # 移除版本号后再分词
    text_clean = _re.sub(r'\d+\.\d+(?:\.\d+)*', ' ', text)

    # 按空格和标点拆分
    raw = _re.split(r'[\s,，。、！？!?\(\)（）\[\]【】]+', text_clean)

    tokens = []
    stop_words = {'的', '了', '是', '在', '有', '和', '与', '就', '也', '都', '还', '这', '那', '给', '用',
                  '我', '你', '他', '它', '们', '吗', '呢', '吧', '啊', '看', '帮', '下', '一下', '看看'}

    for word in raw:
        word = word.strip().lower()
        if not word or word in stop_words:
            continue
        if len(word) <= 1:
            continue

        # 英文词直接保留
        if _re.match(r'^[a-z]+$', word):
            tokens.append(word)
            continue

        # 短中文词（2-4字）直接保留
        if len(word) <= 4:
            tokens.append(word)
        else:
            # 长中文串做 bigram
            tokens.append(word)  # 保留整体
            for i in range(len(word) - 1):
                bi = word[i:i+2]
                if bi not in stop_words:
                    tokens.append(bi)

    # 加回版本号
    tokens.extend(versions)

    return list(dict.fromkeys(tokens))  # 去重保序


def _get_synonyms(word: str) -> list:
    """获取同义词"""
    word_lower = word.lower()
    return _SYNONYMS.get(word_lower, [])


def _is_version(token: str) -> bool:
    """判断是否为版本号"""
    return bool(_re.match(r'^\d+\.\d+', token))


def _is_project_keyword(token: str) -> bool:
    """判断是否为项目关键词"""
    project_words = {'安卓', 'android', 'ios', '苹果', '鸿蒙', 'harmony',
                     '体验服', '正式服', '体验', '正式', 'exp', 'prod'}
    return token.lower() in project_words
