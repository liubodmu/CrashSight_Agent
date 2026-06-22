"""CrashSight API 统一请求层 — 封装签名、重试、限流处理"""
import time
import requests
import urllib3

from .config import CRASHSIGHT_BASE, USER_AUTH
from .auth import build_auth_params

# 禁用 SSL 警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class CrashSightAPIError(Exception):
    """CrashSight API 调用异常"""
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(f"API Error {status_code}: {message}")


def openapi_get(url_path: str, timeout: int = 30) -> dict:
    """
    用 OpenAPI 签名方式发起 GET 请求
    url_path: 完整 URL（不含签名参数）
    """
    auth_params = build_auth_params(USER_AUTH['userId'], USER_AUTH['userKey'])
    sep = '&' if '?' in url_path else '?'
    params_str = '&'.join(f'{k}={v}' for k, v in auth_params.items())
    full_url = f'{url_path}{sep}{params_str}'

    resp = requests.get(
        full_url,
        timeout=timeout,
        headers={'Content-Type': 'application/json', 'Accept-Encoding': '*'},
        verify=False,
    )
    if resp.status_code == 200:
        return resp.json()
    raise CrashSightAPIError(resp.status_code, resp.text[:200])


def openapi_post(url_path: str, body: dict, timeout: int = 30, max_retry: int = 5) -> dict:
    """
    用 OpenAPI 签名方式发起 POST 请求
    自动处理 429 限流和 502/503/504 网关错误重试
    """
    for attempt in range(max_retry):
        auth_params = build_auth_params(USER_AUTH['userId'], USER_AUTH['userKey'])
        sep = '&' if '?' in url_path else '?'
        params_str = '&'.join(f'{k}={v}' for k, v in auth_params.items())
        full_url = f'{url_path}{sep}{params_str}'

        resp = requests.post(
            full_url,
            json=body,
            timeout=timeout,
            headers={'Content-Type': 'application/json', 'Accept-Encoding': '*'},
            verify=False,
        )

        if resp.status_code == 200:
            return resp.json()

        # 限流重试
        if resp.status_code == 429 and attempt < max_retry - 1:
            wait = 3 * (attempt + 1)
            print(f'[API] 429 限流，{wait}s 后重试 ({attempt+1}/{max_retry})')
            time.sleep(wait)
            continue

        # 网关错误重试
        if resp.status_code in (502, 503, 504) and attempt < max_retry - 1:
            wait = 3 * (attempt + 1)
            print(f'[API] {resp.status_code} 网关错误，{wait}s 后重试 ({attempt+1}/{max_retry})')
            time.sleep(wait)
            continue

        raise CrashSightAPIError(resp.status_code, resp.text[:200])

    raise CrashSightAPIError(429, "重试次数耗尽")
