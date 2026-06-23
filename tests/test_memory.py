"""记忆系统单元测试"""
import sys
import os
import tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from crashsight_agent.memory.store import MemoryStore


@pytest.fixture
def memory():
    """使用临时数据库的 MemoryStore"""
    tmp = tempfile.mktemp(suffix='.sqlite')
    store = MemoryStore(db_path=tmp)
    yield store
    os.unlink(tmp)


class TestEpisodicMemory:
    """Episodic Memory 测试"""

    def test_save_and_retrieve(self, memory):
        memory.save_episode('安卓 3.7 昨天', 'crash_report', 'android_exp', '3.7.*')
        episodes = memory.get_recent_episodes(limit=1)
        assert len(episodes) == 1
        assert episodes[0]['query'] == '安卓 3.7 昨天'
        assert episodes[0]['intent'] == 'crash_report'

    def test_find_similar(self, memory):
        memory.save_episode('安卓体验服 3.7 昨天的崩溃', 'crash_report', 'android_exp')
        memory.save_episode('iOS 6.1 今天', 'crash_report', 'ios_exp')
        results = memory.find_similar_episodes('安卓 昨天')
        assert len(results) >= 1
        assert results[0]['project_id'] == 'android_exp'

    def test_find_similar_no_match(self, memory):
        memory.save_episode('安卓 3.7 昨天', 'crash_report', 'android_exp')
        results = memory.find_similar_episodes('完全无关的查询xyz')
        assert len(results) == 0

    def test_success_filter(self, memory):
        memory.save_episode('失败的查询', 'crash_report', success=False)
        results = memory.find_similar_episodes('失败的查询')
        assert len(results) == 0  # 只返回成功的


class TestSkillMemory:
    """Skill Memory 测试"""

    def test_skill_extraction_threshold(self, memory):
        """3次成功才提炼 Skill"""
        memory.save_episode('q1', 'crash_report', 'android_exp')
        memory.save_episode('q2', 'crash_report', 'android_exp')
        skills = memory.get_all_skills()
        assert len(skills) == 0  # 2次不够

        memory.save_episode('q3', 'crash_report', 'android_exp')
        skills = memory.get_all_skills()
        assert len(skills) == 1
        assert skills[0]['intent'] == 'crash_report'
        assert skills[0]['project_id'] == 'android_exp'

    def test_skill_increments(self, memory):
        """重复触发增加 use_count"""
        for i in range(5):
            memory.save_episode(f'query{i}', 'crash_report', 'android_exp')
        skills = memory.get_all_skills()
        assert skills[0]['use_count'] >= 2

    def test_find_skill(self, memory):
        for i in range(3):
            memory.save_episode(f'q{i}', 'trend_query', 'ios_exp')
        skill = memory.find_skill(intent='trend_query', project_id='ios_exp')
        assert skill is not None
        assert skill['intent'] == 'trend_query'


class TestSemanticRule:
    """Semantic Rule 测试"""

    def test_add_and_get_rule(self, memory):
        memory.add_rule('当关键帧是系统库时，应扩大搜索范围', 'history_compare', 0.9)
        rules = memory.get_active_rules('history_compare')
        assert len(rules) == 1
        assert '系统库' in rules[0]['rule_text']

    def test_rule_confidence_filter(self, memory):
        memory.add_rule('高置信规则', 'test', 0.9)
        memory.add_rule('低置信规则', 'test', 0.5)
        rules = memory.get_active_rules('test')
        assert len(rules) == 1  # 只返回 >= 0.7 的

    def test_rule_decay(self, memory):
        memory.add_rule('会衰减的规则', 'test', 0.75)
        memory.decay_rules(decay_rate=0.5)  # 大幅衰减
        rules = memory.get_active_rules('test')
        assert len(rules) == 0  # 衰减后低于 0.3，被失活


class TestStats:
    """统计接口测试"""

    def test_empty_stats(self, memory):
        stats = memory.get_stats()
        assert stats['episodic_count'] == 0
        assert stats['skill_count'] == 0
        assert stats['rule_count'] == 0

    def test_stats_after_data(self, memory):
        for i in range(5):
            memory.save_episode(f'q{i}', 'crash_report', 'android_exp')
        memory.add_rule('rule1', 'test', 0.9)
        stats = memory.get_stats()
        assert stats['episodic_count'] == 5
        assert stats['skill_count'] == 1
        assert stats['rule_count'] == 1
