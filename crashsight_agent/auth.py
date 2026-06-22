"""CrashSight OpenAPI 签名鉴权"""
import hmac
import hashlib
import base64
import time
import uuid


def generate_openapi_signature(user_id: str, user_key: str, timestamp: str) -> str:
    """生成 OpenAPI 签名（HMAC-SHA256 + Base64）"""
    message = f"{user_id}_{timestamp}"
    key_bytes = user_key.encode('utf-8')
    message_bytes = message.encode('utf-8')
    hash_str = hmac.new(key_bytes, message_bytes, digestmod=hashlib.sha256).hexdigest()
    hash_str_64 = base64.b64encode(hash_str.encode('utf-8')).decode('utf-8')
    return hash_str_64


def build_auth_params(user_id: str, user_key: str) -> dict:
    """构建认证参数字典"""
    t = str(int(time.time()))
    user_secret = generate_openapi_signature(user_id, user_key, t)
    fsn = str(uuid.uuid4())
    return {
        'userSecret': user_secret,
        'localUserId': user_id,
        'fsn': fsn,
        't': t,
    }


def build_cookie_header(cookie_auth: dict) -> str:
    """构建 Cookie 认证头"""
    return '; '.join(f'{k}={v}' for k, v in cookie_auth.items() if v)
