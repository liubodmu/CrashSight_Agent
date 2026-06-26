"""熔断器 — 工具连续失败时自动禁用，超时后恢复（线程安全）"""
import time
import logging
import threading
from enum import Enum

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    CLOSED = 'closed'        # 正常
    OPEN = 'open'            # 熔断（禁止调用）
    HALF_OPEN = 'half_open'  # 试探恢复


class CircuitBreaker:
    """
    线程安全的熔断器:
    - 连续失败 failure_threshold 次 → 熔断（OPEN）
    - 熔断后等 recovery_timeout 秒 → 半开（HALF_OPEN）
    - 半开状态下成功 → 恢复（CLOSED）
    - 半开状态下失败 → 继续熔断
    
    所有状态修改都在 Lock 保护下执行，避免多线程竞态。
    """

    def __init__(self, failure_threshold: int = 3, recovery_timeout: int = 60):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_failure_time = 0.0
        self.total_calls = 0
        self.total_failures = 0
        self._lock = threading.Lock()

    def can_execute(self) -> bool:
        """是否允许执行（线程安全）"""
        with self._lock:
            if self.state == CircuitState.CLOSED:
                return True
            if self.state == CircuitState.OPEN:
                if time.time() - self.last_failure_time >= self.recovery_timeout:
                    self.state = CircuitState.HALF_OPEN
                    return True
                return False
            # HALF_OPEN: 允许试探一次
            return True

    def record_success(self):
        """记录成功（线程安全）"""
        with self._lock:
            self.total_calls += 1
            self.failure_count = 0
            if self.state == CircuitState.HALF_OPEN:
                self.state = CircuitState.CLOSED
                logger.info('熔断器恢复正常 (HALF_OPEN → CLOSED)')

    def record_failure(self):
        """记录失败（线程安全）"""
        with self._lock:
            self.total_calls += 1
            self.total_failures += 1
            self.failure_count += 1
            self.last_failure_time = time.time()

            if self.state == CircuitState.HALF_OPEN:
                self.state = CircuitState.OPEN
                logger.warning('试探失败，继续熔断 (HALF_OPEN → OPEN)')
            elif self.failure_count >= self.failure_threshold:
                self.state = CircuitState.OPEN
                logger.warning(f'连续失败{self.failure_count}次，触发熔断 (CLOSED → OPEN)')

    @property
    def is_open(self) -> bool:
        return self.state == CircuitState.OPEN


class ErrorType(Enum):
    """错误分类"""
    RATE_LIMIT = 'rate_limit'        # 429 限流 → 等待后重试
    TIMEOUT = 'timeout'              # 网络超时 → 立即重试
    NOT_FOUND = 'not_found'          # 404/数据不存在 → 不重试
    PARAM_ERROR = 'param_error'      # 参数错误 → 不重试
    SERVER_ERROR = 'server_error'    # 500/502/503 → 等待后重试
    AUTH_ERROR = 'auth_error'        # 401/403 → 不重试，提示配置
    UNKNOWN = 'unknown'              # 未知 → 重试 1 次


def classify_error(error_msg: str) -> ErrorType:
    """根据错误信息分类"""
    msg = str(error_msg).lower()

    if '429' in msg or '限流' in msg or 'rate limit' in msg:
        return ErrorType.RATE_LIMIT
    if 'timeout' in msg or '超时' in msg or 'timed out' in msg:
        return ErrorType.TIMEOUT
    if '404' in msg or '不存在' in msg or 'not found' in msg:
        return ErrorType.NOT_FOUND
    if '400' in msg or '参数' in msg or 'invalid' in msg:
        return ErrorType.PARAM_ERROR
    if '500' in msg or '502' in msg or '503' in msg or '504' in msg:
        return ErrorType.SERVER_ERROR
    if '401' in msg or '403' in msg or 'auth' in msg or '鉴权' in msg:
        return ErrorType.AUTH_ERROR
    return ErrorType.UNKNOWN


# 各错误类型的重试策略
RETRY_POLICY = {
    ErrorType.RATE_LIMIT:   {'should_retry': True,  'wait_seconds': 3, 'max_retries': 3},
    ErrorType.TIMEOUT:      {'should_retry': True,  'wait_seconds': 0, 'max_retries': 2},
    ErrorType.NOT_FOUND:    {'should_retry': False, 'wait_seconds': 0, 'max_retries': 0},
    ErrorType.PARAM_ERROR:  {'should_retry': False, 'wait_seconds': 0, 'max_retries': 0},
    ErrorType.SERVER_ERROR: {'should_retry': True,  'wait_seconds': 3, 'max_retries': 2},
    ErrorType.AUTH_ERROR:   {'should_retry': False, 'wait_seconds': 0, 'max_retries': 0},
    ErrorType.UNKNOWN:      {'should_retry': True,  'wait_seconds': 1, 'max_retries': 1},
}
