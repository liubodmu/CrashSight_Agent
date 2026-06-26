"""circuit_breaker 单元测试"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from crashsight_agent.tools.circuit_breaker import (
    CircuitBreaker, CircuitState, classify_error, ErrorType, RETRY_POLICY
)


class TestCircuitBreaker:
    """熔断器状态机测试"""

    def test_initial_state_closed(self):
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=60)
        assert cb.state == CircuitState.CLOSED
        assert cb.can_execute() is True

    def test_opens_after_threshold_failures(self):
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=60)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.CLOSED
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        assert cb.can_execute() is False

    def test_success_resets_failure_count(self):
        cb = CircuitBreaker(failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        assert cb.failure_count == 0
        assert cb.state == CircuitState.CLOSED
        # 需要重新连续失败 3 次才会熔断
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.CLOSED

    def test_half_open_after_recovery_timeout(self):
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0)  # 立即恢复
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        # recovery_timeout=0 → 立即进入 HALF_OPEN
        assert cb.can_execute() is True
        assert cb.state == CircuitState.HALF_OPEN

    def test_half_open_success_closes(self):
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0)
        cb.record_failure()
        cb.record_failure()
        cb.can_execute()  # → HALF_OPEN
        cb.record_success()
        assert cb.state == CircuitState.CLOSED

    def test_half_open_failure_reopens(self):
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0)
        cb.record_failure()
        cb.record_failure()
        cb.can_execute()  # → HALF_OPEN
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

    def test_total_calls_tracking(self):
        cb = CircuitBreaker(failure_threshold=5)
        cb.record_success()
        cb.record_success()
        cb.record_failure()
        assert cb.total_calls == 3
        assert cb.total_failures == 1


class TestClassifyError:
    """错误分类测试"""

    def test_rate_limit(self):
        assert classify_error('429 Too Many Requests') == ErrorType.RATE_LIMIT
        assert classify_error('限流了') == ErrorType.RATE_LIMIT

    def test_timeout(self):
        assert classify_error('Connection timed out') == ErrorType.TIMEOUT
        assert classify_error('请求超时') == ErrorType.TIMEOUT

    def test_not_found(self):
        assert classify_error('404 Not Found') == ErrorType.NOT_FOUND
        assert classify_error('数据不存在') == ErrorType.NOT_FOUND

    def test_server_error(self):
        assert classify_error('502 Bad Gateway') == ErrorType.SERVER_ERROR
        assert classify_error('503 Service Unavailable') == ErrorType.SERVER_ERROR

    def test_auth_error(self):
        assert classify_error('401 Unauthorized') == ErrorType.AUTH_ERROR
        assert classify_error('鉴权失败') == ErrorType.AUTH_ERROR

    def test_unknown(self):
        assert classify_error('something weird happened') == ErrorType.UNKNOWN


class TestRetryPolicy:
    """重试策略配置测试"""

    def test_rate_limit_should_retry(self):
        policy = RETRY_POLICY[ErrorType.RATE_LIMIT]
        assert policy['should_retry'] is True
        assert policy['max_retries'] >= 2

    def test_not_found_should_not_retry(self):
        policy = RETRY_POLICY[ErrorType.NOT_FOUND]
        assert policy['should_retry'] is False

    def test_auth_error_should_not_retry(self):
        policy = RETRY_POLICY[ErrorType.AUTH_ERROR]
        assert policy['should_retry'] is False
