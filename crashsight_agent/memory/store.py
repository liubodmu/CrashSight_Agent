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
        """查找相似的历史查询（简单关键词匹配，后续可升级为 Embedding）"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row

        # 提取查询中的关键词
        keywords = [w for w in query.replace('，', ' ').replace('、', ' ').split() if len(w) >= 2]

        if not keywords:
            conn.close()
            return []

        # 用 LIKE 做模糊匹配（每个关键词）
        conditions = ' OR '.join(['query LIKE ?' for _ in keywords])
        params = [f'%{kw}%' for kw in keywords]

        rows = conn.execute(
            f"""SELECT * FROM episodic WHERE success=1 AND ({conditions})
                ORDER BY created_at DESC LIMIT ?""",
            params + [limit]
        ).fetchall()
        conn.close()

        return [dict(row) for row in rows]

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
