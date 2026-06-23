"""Route 节点单元测试 — 意图识别 + 参数解析"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from datetime import datetime, timedelta
from crashsight_agent.orchestration.nodes.route import _layer1_keyword_match


TODAY = datetime.now()
YESTERDAY = (TODAY - timedelta(days=1)).strftime('%Y%m%d')
TODAY_STR = TODAY.strftime('%Y%m%d')


class TestIntentRecognition:
    """意图识别测试"""

    def test_crash_report_basic(self):
        r = _layer1_keyword_match('安卓体验服 3.7 昨天的崩溃', TODAY)
        assert r['matched'] is True
        assert r['intent'] == 'crash_report'

    def test_crash_report_top(self):
        r = _layer1_keyword_match('安卓 top10 昨天', TODAY)
        assert r['matched'] is True
        assert r['intent'] == 'crash_report'

    def test_trend_query(self):
        r = _layer1_keyword_match('安卓崩溃率趋势', TODAY)
        assert r['matched'] is True
        assert r['intent'] == 'trend_query'

    def test_trend_walk(self):
        r = _layer1_keyword_match('iOS 崩溃率走势最近一周', TODAY)
        assert r['matched'] is True
        assert r['intent'] == 'trend_query'

    def test_history_check(self):
        r = _layer1_keyword_match('Top1 正式服有没有', TODAY)
        assert r['matched'] is True
        assert r['intent'] == 'history_check'

    def test_history_old_issue(self):
        r = _layer1_keyword_match('这个问题是历史问题吗', TODAY)
        assert r['matched'] is True
        assert r['intent'] == 'history_check'

    def test_issue_detail(self):
        r = _layer1_keyword_match('Top1 完整堆栈', TODAY)
        assert r['matched'] is True
        assert r['intent'] == 'issue_detail'

    def test_compare(self):
        r = _layer1_keyword_match('对比这周和上周的崩溃', TODAY)
        assert r['matched'] is True
        assert r['intent'] == 'compare'

    def test_unknown_returns_not_matched(self):
        r = _layer1_keyword_match('今天天气怎么样', TODAY)
        assert r['matched'] is False


class TestProjectParsing:
    """项目解析测试"""

    def test_android_exp(self):
        r = _layer1_keyword_match('安卓体验服 3.7 昨天', TODAY)
        assert r['project_id'] == 'android_exp'

    def test_android_prod(self):
        r = _layer1_keyword_match('安卓正式服 3.7 昨天', TODAY)
        assert r['project_id'] == 'android_prod'

    def test_ios_exp(self):
        r = _layer1_keyword_match('iOS体验服 6.1 昨天', TODAY)
        assert r['project_id'] == 'ios_exp'

    def test_harmony_exp(self):
        r = _layer1_keyword_match('鸿蒙体验服 2.1 昨天', TODAY)
        assert r['project_id'] == 'harmony_exp'

    def test_default_android_exp(self):
        """只说安卓默认体验服"""
        r = _layer1_keyword_match('安卓 3.7 昨天的崩溃', TODAY)
        assert r['project_id'] == 'android_exp'


class TestVersionParsing:
    """版本号解析测试"""

    def test_short_version(self):
        r = _layer1_keyword_match('安卓 3.7 昨天', TODAY)
        assert r['version'] == '3.7.*'

    def test_full_version(self):
        r = _layer1_keyword_match('安卓 3.7.365.365.365 昨天', TODAY)
        assert r['version'] == '3.7.365.365.365'

    def test_three_part_version(self):
        r = _layer1_keyword_match('安卓 6.1.2 昨天', TODAY)
        assert r['version'] == '6.1.2'

    def test_no_version(self):
        r = _layer1_keyword_match('安卓体验服昨天的崩溃', TODAY)
        assert r['version'] is None

    def test_multi_version(self):
        """逗号分隔多版本"""
        r = _layer1_keyword_match('安卓 3.7.375.375.375,3.7.376.376.376 昨天', TODAY)
        assert '3.7.375.375.375' in r['version']
        assert '3.7.376.376.376' in r['version']


class TestDateParsing:
    """日期解析测试"""

    def test_yesterday(self):
        r = _layer1_keyword_match('安卓 3.7 昨天', TODAY)
        assert r['start_date'] == YESTERDAY
        assert r['end_date'] == YESTERDAY

    def test_today(self):
        r = _layer1_keyword_match('安卓 3.7 今天', TODAY)
        assert r['start_date'] == TODAY_STR
        assert r['end_date'] == TODAY_STR

    def test_last_week(self):
        r = _layer1_keyword_match('安卓最近一周', TODAY)
        expected_start = (TODAY - timedelta(days=6)).strftime('%Y%m%d')
        assert r['start_date'] == expected_start
        assert r['end_date'] == TODAY_STR

    def test_last_30days(self):
        r = _layer1_keyword_match('安卓最近30天', TODAY)
        expected_start = (TODAY - timedelta(days=29)).strftime('%Y%m%d')
        assert r['start_date'] == expected_start

    def test_iso_date_range(self):
        """ISO日期范围: 2026-06-17~2026-06-23（需要包含能命中意图的关键词）"""
        r = _layer1_keyword_match('安卓体验服 3.7 2026-06-17~2026-06-23 的崩溃top10', TODAY)
        assert r['matched'] is True
        assert r['start_date'] == '20260617'
        assert r['end_date'] == '20260623'

    def test_iso_date_slash(self):
        """斜杠格式: 2026/06/17~2026/06/23"""
        r = _layer1_keyword_match('安卓体验服 2026/06/01~2026/06/15 崩溃数据', TODAY)
        assert r['matched'] is True
        assert r['start_date'] == '20260601'
        assert r['end_date'] == '20260615'

    def test_no_date(self):
        r = _layer1_keyword_match('安卓崩溃率趋势', TODAY)
        assert r['start_date'] is None


class TestMissingParams:
    """缺失参数检测测试"""

    def test_crash_report_missing_version(self):
        """crash_report 缺版本 → missing_params 包含 version"""
        r = _layer1_keyword_match('安卓体验服昨天的崩溃', TODAY)
        assert 'version' in r.get('missing_params', [])

    def test_crash_report_missing_date(self):
        """crash_report 缺日期"""
        r = _layer1_keyword_match('安卓体验服 3.7 崩溃分析', TODAY)
        assert 'date_range' in r.get('missing_params', [])

    def test_crash_report_complete(self):
        """参数齐全 → missing_params 为空"""
        r = _layer1_keyword_match('安卓体验服 3.7 昨天', TODAY)
        assert r.get('missing_params', []) == []
