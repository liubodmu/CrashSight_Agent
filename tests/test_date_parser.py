"""日期解析器单元测试"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from datetime import datetime, timedelta
from crashsight_agent.utils.date_parser import parse_date_range


TODAY = datetime.now()
TODAY_STR = TODAY.strftime('%Y%m%d')
YESTERDAY_STR = (TODAY - timedelta(days=1)).strftime('%Y%m%d')


class TestChineseDateKeywords:
    """中文日期关键词"""

    def test_yesterday(self):
        s, e = parse_date_range('昨天')
        assert s == YESTERDAY_STR
        assert e == YESTERDAY_STR

    def test_today(self):
        s, e = parse_date_range('今天')
        assert s == TODAY_STR
        assert e == TODAY_STR

    def test_last_week_keyword(self):
        s, e = parse_date_range('最近一周')
        expected = (TODAY - timedelta(days=6)).strftime('%Y%m%d')
        assert s == expected
        assert e == TODAY_STR

    def test_last_3days(self):
        s, e = parse_date_range('最近3天')
        expected = (TODAY - timedelta(days=2)).strftime('%Y%m%d')
        assert s == expected

    def test_last_30days(self):
        s, e = parse_date_range('最近30天')
        expected = (TODAY - timedelta(days=29)).strftime('%Y%m%d')
        assert s == expected

    def test_last_month(self):
        s, e = parse_date_range('最近一个月')
        expected = (TODAY - timedelta(days=29)).strftime('%Y%m%d')
        assert s == expected

    def test_prev_week(self):
        s, e = parse_date_range('上周')
        # 上周一
        days_since_monday = TODAY.weekday()
        last_monday = TODAY - timedelta(days=days_since_monday + 7)
        last_sunday = last_monday + timedelta(days=6)
        assert s == last_monday.strftime('%Y%m%d')
        assert e == last_sunday.strftime('%Y%m%d')


class TestISODateFormat:
    """ISO 日期格式"""

    def test_dash_range(self):
        """2026-06-17~2026-06-23"""
        s, e = parse_date_range('2026-06-17~2026-06-23')
        assert s == '20260617'
        assert e == '20260623'

    def test_slash_range(self):
        """2026/06/01~2026/06/15"""
        s, e = parse_date_range('2026/06/01~2026/06/15')
        assert s == '20260601'
        assert e == '20260615'

    def test_to_separator(self):
        """2026-01-01到2026-01-31"""
        s, e = parse_date_range('2026-01-01到2026-01-31')
        assert s == '20260101'
        assert e == '20260131'

    def test_single_date(self):
        """单个日期"""
        s, e = parse_date_range('2026-06-20')
        assert s == '20260620'
        assert e == '20260620'

    def test_single_date_slash(self):
        s, e = parse_date_range('2026/03/15')
        assert s == '20260315'
        assert e == '20260315'


class TestEdgeCases:
    """边界情况"""

    def test_empty_string(self):
        s, e = parse_date_range('')
        assert s is None
        assert e is None

    def test_no_date_info(self):
        s, e = parse_date_range('安卓崩溃数据')
        assert s is None
        assert e is None

    def test_whitespace(self):
        s, e = parse_date_range('   昨天   ')
        assert s == YESTERDAY_STR
