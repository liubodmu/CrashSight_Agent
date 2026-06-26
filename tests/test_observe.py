"""Observe 节点单元测试 — 异常检测 + 恢复策略"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from crashsight_agent.orchestration.nodes.observe import (
    observe_node, _detect_anomaly, _plan_recovery
)


class TestObserveBasicFailure:
    """基本失败检测"""

    def test_all_failed_triggers_retry(self):
        """所有工具失败 → 触发重试"""
        state = {
            'intent': 'crash_report',
            'observations': [
                {'alias': 'trend', 'success': False, 'error': 'timeout'},
                {'alias': 'top_issues', 'success': False, 'error': 'timeout'},
            ],
            'retry_count': 0,
            'recover_count': 0,
            'project_id': 'android_exp',
            'issue_id': '',
        }
        result = observe_node(state)
        assert result['final_status'] == 'retry'
        assert result['retry_count'] == 1

    def test_all_failed_exhausted(self):
        """重试耗尽 → 错误状态"""
        state = {
            'intent': 'crash_report',
            'observations': [
                {'alias': 'trend', 'success': False, 'error': 'timeout'},
            ],
            'retry_count': 2,
            'recover_count': 0,
            'project_id': 'android_exp',
            'issue_id': '',
        }
        result = observe_node(state)
        assert result['final_status'] == 'error'

    def test_partial_success_is_ok(self):
        """部分成功 → 正常结束"""
        state = {
            'intent': 'trend_query',
            'observations': [
                {'alias': 'trend', 'success': True, 'data': {'minRate': '0.1'}},
            ],
            'retry_count': 0,
            'recover_count': 0,
            'project_id': 'android_exp',
            'issue_id': '',
        }
        result = observe_node(state)
        assert result['final_status'] == 'ok'


class TestDetectAnomaly:
    """异常模式检测"""

    def test_all_stacks_empty(self):
        """所有堆栈为空检测"""
        observations = [{
            'alias': 'top_issues',
            'success': True,
            'data': [
                {'issueId': f'issue_{i}', 'historyDetail': {'reason': '堆栈为空'}}
                for i in range(10)
            ],
        }]
        anomaly = _detect_anomaly(observations, 'crash_report')
        assert anomaly is not None
        assert anomaly['type'] == 'all_stacks_empty'

    def test_no_anomaly_when_stacks_present(self):
        """有堆栈时无异常"""
        observations = [{
            'alias': 'top_issues',
            'success': True,
            'data': [
                {'issueId': f'issue_{i}', 'historyDetail': {'reason': 'ok'}, 'isHistoryIssue': True}
                for i in range(5)
            ] + [
                {'issueId': f'issue_{i+5}', 'historyDetail': {'reason': '堆栈为空'}}
                for i in range(3)
            ],
        }]
        anomaly = _detect_anomaly(observations, 'crash_report')
        assert anomaly is None

    def test_trend_zero_but_has_crashes(self):
        """趋势为0但有崩溃"""
        observations = [
            {
                'alias': 'trend', 'success': True,
                'data': {'maxRate': 0, 'minRate': 0},
            },
            {
                'alias': 'top_issues', 'success': True,
                'data': [
                    {'issueId': 'a', 'crashCount': 100, 'historyDetail': {}}
                    for _ in range(5)
                ],
            },
        ]
        anomaly = _detect_anomaly(observations, 'crash_report')
        assert anomaly is not None
        assert anomaly['type'] == 'trend_zero_but_has_crashes'


class TestPlanRecovery:
    """恢复策略规划"""

    def test_all_stacks_empty_first_recovery(self):
        """堆栈全空 → 用 keyStack 兜底"""
        anomaly = {'type': 'all_stacks_empty', 'data': {}}
        state = {'recover_count': 0}
        recovery = _plan_recovery(anomaly, state)
        assert recovery is not None
        assert recovery['adjustments'].get('use_key_stack_fallback') is True

    def test_all_stacks_empty_second_recovery(self):
        """堆栈全空（第二次）→ 跳过堆栈"""
        anomaly = {'type': 'all_stacks_empty', 'data': {}}
        state = {'recover_count': 1}
        recovery = _plan_recovery(anomaly, state)
        assert recovery is not None
        assert recovery['adjustments'].get('skip_stack_and_history') is True

    def test_trend_zero_recovery(self):
        """趋势为0 → 全版本重拉"""
        anomaly = {'type': 'trend_zero_but_has_crashes', 'data': {}}
        state = {'recover_count': 0}
        recovery = _plan_recovery(anomaly, state)
        assert recovery is not None
        assert recovery['adjustments'].get('trend_all_versions') is True

    def test_no_recovery_when_exhausted(self):
        """恢复次数用尽 → 无策略"""
        anomaly = {'type': 'all_stacks_empty', 'data': {}}
        state = {'recover_count': 2}
        recovery = _plan_recovery(anomaly, state)
        assert recovery is None
