"""CrashSight API 统一请求层 — 封装签名、重试、限流处理

改进点:
- GET/POST 统一重试逻辑
- 启用 SSL 验证（可通过环境变量 CS_VERIFY_SSL=0 关闭，仅限调试）
- 使用 logging 替代 print
- 指数退避重试
"""
import os
import time
import logging
import requests
from urllib.parse import urlencode, urlparse, parse_qs, urlunparse, urljoin

from .config import CRASHSIGHT_BASE, USER_AUTH, API_TIMEOUT
from .auth import build_auth_params

logger = logging.getLogger(__name__)

# SSL 验证：默认开启，可通过环境变量关闭（仅限内网调试）
_VERIFY_SSL = os.getenv('CS_VERIFY_SSL', '1').lower() not in ('0', 'false', 'no')
if not _VERIFY_SSL:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    logger.warning('SSL 验证已禁用（CS_VERIFY_SSL=0），仅限内网调试使用')

# 重试配置
_DEFAULT_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 2.0
_RETRY_MAX_DELAY = 15.0
_RETRYABLE_STATUS_CODES = {429, 502, 503, 504}


class CrashSightAPIError(Exception):
    """CrashSight API 调用异常"""
    def __init__(self, status_code: int, message: str, url: str = ''):
        self.status_code = status_code
        self.message = message
        self.url = url
        super().__init__(f"API Error {status_code}: {message}")

    @property
    def is_retryable(self) -> bool:
        return self.status_code in _RETRYABLE_STATUS_CODES

    @property
    def is_auth_error(self) -> bool:
        return self.status_code in (401, 403)


def _build_signed_url(url_path: str) -> str:
    """为 URL 添加签名参数"""
    auth_params = build_auth_params(USER_AUTH['userId'], USER_AUTH['userKey'])
    sep = '&' if '?' in url_path else '?'
    params_str = urlencode(auth_params)
    return f'{url_path}{sep}{params_str}'


def _get_retry_delay(attempt: int) -> float:
    """指数退避延迟"""
    delay = _RETRY_BASE_DELAY * (2 ** attempt)
    return min(delay, _RETRY_MAX_DELAY)


def _request_with_retry(
    method: str,
    url_path: str,
    body: dict = None,
    timeout: int = None,
    max_retries: int = _DEFAULT_MAX_RETRIES,
) -> dict:
    """统一的 HTTP 请求 + 重试逻辑

    Args:
        method: 'GET' 或 'POST'
        url_path: 完整 URL（不含签名参数）
        body: POST 请求体（仅 POST 时使用）
        timeout: 超时秒数
        max_retries: 最大重试次数

    Returns:
        API 响应 JSON

    Raises:
        CrashSightAPIError: API 调用失败
        requests.exceptions.Timeout: 超时（重试耗尽后）
    """
    if timeout is None:
        timeout = API_TIMEOUT

    headers = {'Content-Type': 'application/json', 'Accept-Encoding': '*'}
    last_error = None

    for attempt in range(max_retries + 1):
        full_url = _build_signed_url(url_path)

        try:
            if method.upper() == 'GET':
                resp = requests.get(
                    full_url, timeout=timeout, headers=headers, verify=_VERIFY_SSL,
                )
            else:
                resp = requests.post(
                    full_url, json=body, timeout=timeout, headers=headers, verify=_VERIFY_SSL,
                )

            if resp.status_code == 200:
                return resp.json()

            # 可重试的错误
            if resp.status_code in _RETRYABLE_STATUS_CODES and attempt < max_retries:
                delay = _get_retry_delay(attempt)
                logger.warning(
                    f'API {resp.status_code}，{delay:.1f}s 后重试 '
                    f'({attempt + 1}/{max_retries}) url={url_path[:80]}'
                )
                time.sleep(delay)
                last_error = CrashSightAPIError(resp.status_code, resp.text[:200], url_path)
                continue

            # 不可重试的错误
            raise CrashSightAPIError(resp.status_code, resp.text[:200], url_path)

        except requests.exceptions.Timeout as e:
            last_error = e
            if attempt < max_retries:
                delay = _get_retry_delay(attempt)
                logger.warning(
                    f'API 超时，{delay:.1f}s 后重试 ({attempt + 1}/{max_retries}) url={url_path[:80]}'
                )
                time.sleep(delay)
            else:
                logger.error(f'API 超时，重试 {max_retries} 次仍失败: {url_path[:80]}')
                raise CrashSightAPIError(408, f'请求超时 ({timeout}s)', url_path) from e

        except requests.exceptions.ConnectionError as e:
            last_error = e
            if attempt < max_retries:
                delay = _get_retry_delay(attempt)
                logger.warning(
                    f'API 连接失败，{delay:.1f}s 后重试 ({attempt + 1}/{max_retries}): {e}'
                )
                time.sleep(delay)
            else:
                logger.error(f'API 连接失败，重试 {max_retries} 次仍失败: {e}')
                raise CrashSightAPIError(0, f'连接失败: {str(e)[:100]}', url_path) from e

    # 不应该走到这里，但以防万一
    if last_error:
        raise CrashSightAPIError(429, "重试次数耗尽", url_path)
    raise CrashSightAPIError(0, "未知错误", url_path)


def openapi_get(url_path: str, timeout: int = None, max_retries: int = _DEFAULT_MAX_RETRIES) -> dict:
    """用 OpenAPI 签名方式发起 GET 请求（带重试）

    Args:
        url_path: 完整 URL（不含签名参数）
        timeout: 超时秒数（默认使用 config.API_TIMEOUT）
        max_retries: 最大重试次数

    Returns:
        API 响应 JSON
    """
    return _request_with_retry('GET', url_path, timeout=timeout, max_retries=max_retries)


def openapi_post(url_path: str, body: dict, timeout: int = None, max_retries: int = _DEFAULT_MAX_RETRIES) -> dict:
    """用 OpenAPI 签名方式发起 POST 请求（带重试）

    Args:
        url_path: 完整 URL（不含签名参数）
        body: 请求体字典
        timeout: 超时秒数（默认使用 config.API_TIMEOUT）
        max_retries: 最大重试次数

    Returns:
        API 响应 JSON
    """
    return _request_with_retry('POST', url_path, body=body, timeout=timeout, max_retries=max_retries)
