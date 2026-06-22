"""令牌桶限流器 + Semaphore 并发控制"""
import time
import asyncio


class TokenBucket:
    """令牌桶限流器
    
    控制 API 请求速率不超过 rate 次/per 秒
    线程安全（asyncio 版本）
    """

    def __init__(self, rate: int = 25, per: float = 60.0):
        """
        rate: 每 per 秒允许的最大请求数
        per: 时间窗口（秒）
        """
        self.rate = rate
        self.per = per
        self.tokens = float(rate)
        self.max_tokens = float(rate)
        self.last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self):
        """获取一个令牌，如果没有可用令牌则等待"""
        async with self._lock:
            while True:
                self._refill()
                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return
                # 计算需要等多久才能有一个令牌
                wait_time = (1.0 - self.tokens) / (self.rate / self.per)
                await asyncio.sleep(min(wait_time, 1.0))

    def _refill(self):
        """补充令牌"""
        now = time.monotonic()
        elapsed = now - self.last_refill
        new_tokens = elapsed * (self.rate / self.per)
        self.tokens = min(self.max_tokens, self.tokens + new_tokens)
        self.last_refill = now

    @property
    def available(self) -> int:
        """当前可用令牌数"""
        self._refill()
        return int(self.tokens)


# 全局实例：CrashSight API 限制 25次/分钟
api_limiter = TokenBucket(rate=22, per=60.0)  # 留3个余量，实际限制25

# 并发控制：最多3个请求同时进行
api_semaphore = asyncio.Semaphore(3)
