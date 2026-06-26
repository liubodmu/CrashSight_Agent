"""LLM 调用客户端 — 统一封装 OpenAI 兼容接口（线程安全 + 重试 + 结构化异常）"""
import time
import logging
import threading
from openai import OpenAI, APITimeoutError, RateLimitError, APIConnectionError, APIStatusError
from .config import LLM_MODEL, LLM_API_KEY, LLM_BASE_URL

logger = logging.getLogger(__name__)

# ==================== 自定义异常 ====================


class LLMError(Exception):
    """LLM 调用异常基类"""
    pass


class LLMNotConfiguredError(LLMError):
    """LLM API Key 未配置"""
    pass


class LLMRateLimitError(LLMError):
    """LLM 限流"""
    pass


class LLMTimeoutError(LLMError):
    """LLM 调用超时"""
    pass


class LLMResponseError(LLMError):
    """LLM 返回异常（空回复等）"""
    pass


# ==================== 重试配置 ====================

DEFAULT_MAX_RETRIES = 3
DEFAULT_TIMEOUT = 30  # 秒
DEFAULT_MAX_TOKENS = 2000

# 指数退避参数
_RETRY_BASE_DELAY = 1.0   # 首次重试等待 1s
_RETRY_MAX_DELAY = 10.0   # 最大等待 10s
_RETRY_MULTIPLIER = 2.0   # 指数系数

# ==================== 客户端单例 ====================

_client = None
_client_lock = threading.Lock()


def get_client() -> OpenAI:
    """获取 OpenAI 客户端单例（线程安全）"""
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = OpenAI(
                    api_key=LLM_API_KEY,
                    base_url=LLM_BASE_URL,
                    timeout=DEFAULT_TIMEOUT,
                    max_retries=0,  # 我们自己管理重试
                )
    return _client


# ==================== 重试工具 ====================

def _should_retry(error: Exception) -> bool:
    """判断是否应该重试"""
    if isinstance(error, (APITimeoutError, APIConnectionError)):
        return True
    if isinstance(error, RateLimitError):
        return True
    if isinstance(error, APIStatusError) and error.status_code >= 500:
        return True
    return False


def _get_retry_delay(attempt: int) -> float:
    """计算指数退避延迟（带上限）"""
    delay = _RETRY_BASE_DELAY * (_RETRY_MULTIPLIER ** attempt)
    return min(delay, _RETRY_MAX_DELAY)


# ==================== 核心调用函数 ====================

def call_llm(
    prompt: str,
    system: str = None,
    temperature: float = 0.3,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    max_retries: int = DEFAULT_MAX_RETRIES,
    timeout: int = DEFAULT_TIMEOUT,
) -> str:
    """LLM 文本生成调用（带重试 + 指数退避 + 结构化异常）

    Args:
        prompt: 用户提示词
        system: 系统提示词（可选）
        temperature: 温度参数
        max_tokens: 最大生成 token 数
        max_retries: 最大重试次数
        timeout: 单次调用超时（秒）

    Returns:
        生成的文本

    Raises:
        LLMNotConfiguredError: API Key 未配置
        LLMRateLimitError: 限流（重试耗尽后）
        LLMTimeoutError: 超时（重试耗尽后）
        LLMError: 其他 LLM 错误
    """
    if not LLM_API_KEY:
        logger.warning('LLM API Key 未配置，跳过调用')
        return ''

    messages = []
    if system:
        messages.append({'role': 'system', 'content': system})
    messages.append({'role': 'user', 'content': prompt})

    last_error = None
    for attempt in range(max_retries + 1):
        try:
            client = get_client()
            resp = client.chat.completions.create(
                model=LLM_MODEL,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
            )
            content = resp.choices[0].message.content or ''
            if not content:
                logger.warning(f'LLM 返回空内容 (attempt={attempt + 1})')
            return content

        except RateLimitError as e:
            last_error = e
            if attempt < max_retries:
                delay = _get_retry_delay(attempt)
                logger.warning(f'LLM 限流，{delay:.1f}s 后重试 ({attempt + 1}/{max_retries})')
                time.sleep(delay)
            else:
                logger.error(f'LLM 限流，重试 {max_retries} 次仍失败')
                return ''

        except (APITimeoutError, APIConnectionError) as e:
            last_error = e
            if attempt < max_retries:
                delay = _get_retry_delay(attempt)
                logger.warning(f'LLM 超时/连接失败，{delay:.1f}s 后重试 ({attempt + 1}/{max_retries}): {e}')
                time.sleep(delay)
            else:
                logger.error(f'LLM 超时/连接失败，重试 {max_retries} 次仍失败: {e}')
                return ''

        except APIStatusError as e:
            last_error = e
            if e.status_code >= 500 and attempt < max_retries:
                delay = _get_retry_delay(attempt)
                logger.warning(f'LLM 服务端错误 ({e.status_code})，{delay:.1f}s 后重试 ({attempt + 1}/{max_retries})')
                time.sleep(delay)
            else:
                logger.error(f'LLM API 错误 ({e.status_code}): {e.message}')
                return ''

        except Exception as e:
            logger.error(f'LLM 调用异常: {type(e).__name__}: {e}')
            return ''

    return ''


def call_llm_with_tools(
    messages: list,
    tools: list,
    temperature: float = 0.3,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    max_retries: int = DEFAULT_MAX_RETRIES,
    timeout: int = DEFAULT_TIMEOUT,
):
    """带 Function Calling 的调用（带重试），返回完整 response message

    Args:
        messages: 对话消息列表
        tools: 工具定义列表
        temperature: 温度参数
        max_tokens: 最大生成 token 数
        max_retries: 最大重试次数
        timeout: 单次调用超时（秒）

    Returns:
        OpenAI ChatCompletionMessage 或 None
    """
    if not LLM_API_KEY:
        logger.warning('LLM API Key 未配置')
        return None

    for attempt in range(max_retries + 1):
        try:
            client = get_client()
            resp = client.chat.completions.create(
                model=LLM_MODEL,
                messages=messages,
                tools=tools,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
            )
            return resp.choices[0].message

        except (RateLimitError, APITimeoutError, APIConnectionError) as e:
            if attempt < max_retries:
                delay = _get_retry_delay(attempt)
                logger.warning(f'LLM tool call 失败，{delay:.1f}s 后重试 ({attempt + 1}/{max_retries}): {e}')
                time.sleep(delay)
            else:
                logger.error(f'LLM tool call 重试耗尽: {e}')
                return None

        except APIStatusError as e:
            if e.status_code >= 500 and attempt < max_retries:
                delay = _get_retry_delay(attempt)
                logger.warning(f'LLM tool call 服务端错误 ({e.status_code})，重试中...')
                time.sleep(delay)
            else:
                logger.error(f'LLM tool call API 错误 ({e.status_code}): {e.message}')
                return None

        except Exception as e:
            logger.error(f'LLM tool call 异常: {type(e).__name__}: {e}')
            return None

    return None
