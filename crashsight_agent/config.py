"""项目配置 + 鉴权信息

所有敏感凭据必须通过 .env 文件或环境变量提供，禁止硬编码。
首次使用请复制 .env.example 为 .env 并填入真实值。
"""
import os
import sys
from dotenv import load_dotenv

load_dotenv()


def _require_env(key: str, description: str = '') -> str:
    """获取必需的环境变量，未配置时给出明确提示"""
    value = os.getenv(key, '')
    if not value:
        print(f'[Config] ⚠️  环境变量 {key} 未配置{f" ({description})" if description else ""}，'
              f'请在 .env 文件中设置。', file=sys.stderr)
    return value


# ==================== CrashSight 项目配置 ====================
# appId/appKey 从环境变量读取，按 CS_{PROJECT}_APP_ID / CS_{PROJECT}_APP_KEY 命名
PROJECTS = {
    'android_exp': {
        'name': 'UAMobile体验服_Android',
        'aliases': ['安卓体验服', '安卓体验', 'android体验', 'android体验服', '安卓exp'],
        'platform': 1,
        'pid': 1,
        'appId': os.getenv('CS_ANDROID_EXP_APP_ID', ''),
        'appKey': os.getenv('CS_ANDROID_EXP_APP_KEY', ''),
        'isExperience': True,
        'prod_counterpart': 'android_prod',
    },
    'android_prod': {
        'name': 'UAMobile正式服_Android',
        'aliases': ['安卓正式服', '安卓正式', 'android正式', 'android正式服', '安卓prod'],
        'platform': 1,
        'pid': 1,
        'appId': os.getenv('CS_ANDROID_PROD_APP_ID', ''),
        'appKey': os.getenv('CS_ANDROID_PROD_APP_KEY', ''),
        'isExperience': False,
        'prod_counterpart': None,
    },
    'ios_exp': {
        'name': 'UAMobile体验服_iOS',
        'aliases': ['ios体验服', 'ios体验', '苹果体验', '苹果体验服', 'iOS体验'],
        'platform': 2,
        'pid': 2,
        'appId': os.getenv('CS_IOS_EXP_APP_ID', ''),
        'appKey': os.getenv('CS_IOS_EXP_APP_KEY', ''),
        'isExperience': True,
        'prod_counterpart': 'ios_prod',
    },
    'ios_prod': {
        'name': 'UAMobile正式服_iOS',
        'aliases': ['ios正式服', 'ios正式', '苹果正式', '苹果正式服', 'iOS正式'],
        'platform': 2,
        'pid': 2,
        'appId': os.getenv('CS_IOS_PROD_APP_ID', ''),
        'appKey': os.getenv('CS_IOS_PROD_APP_KEY', ''),
        'isExperience': False,
        'prod_counterpart': None,
    },
    'harmony_exp': {
        'name': 'UAMobile体验版_Harmony',
        'aliases': ['鸿蒙体验', '鸿蒙体验版', '鸿蒙体验服', 'harmony体验', '鸿蒙exp'],
        'platform': 3,
        'pid': 40,
        'appId': os.getenv('CS_HARMONY_EXP_APP_ID', ''),
        'appKey': os.getenv('CS_HARMONY_EXP_APP_KEY', ''),
        'isExperience': True,
        'prod_counterpart': 'harmony_prod',
    },
    'harmony_prod': {
        'name': 'UAMobile正式服_Harmony',
        'aliases': ['鸿蒙正式', '鸿蒙正式版', '鸿蒙正式服', 'harmony正式', '鸿蒙prod'],
        'platform': 3,
        'pid': 40,
        'appId': os.getenv('CS_HARMONY_PROD_APP_ID', ''),
        'appKey': os.getenv('CS_HARMONY_PROD_APP_KEY', ''),
        'isExperience': False,
        'prod_counterpart': None,
    },
}

# ==================== CrashSight 鉴权 ====================
USER_AUTH = {
    'userId': _require_env('CS_USER_ID', 'CrashSight 用户ID'),
    'userKey': _require_env('CS_USER_KEY', 'CrashSight 用户密钥'),
}

CRASHSIGHT_BASE = os.getenv('CS_BASE_URL', 'https://crashsight.qq.com')

# Cookie 认证（OpenAPI 不支持的接口用）
COOKIE_AUTH = {
    'token-skey': os.getenv('CS_TOKEN_SKEY', ''),
    'token-lifeTime': os.getenv('CS_TOKEN_LIFETIME', ''),
    'crashsight_session_cnprod': os.getenv('CS_SESSION', ''),
    'crashsight_gateway_session': os.getenv('CS_GATEWAY_SESSION', ''),
}

# ==================== TAPD 配置 ====================
TAPD_TOKEN = _require_env('TAPD_TOKEN', 'TAPD API Token')
TAPD_API = os.getenv('TAPD_API_URL', 'https://apiv2.tapd.woa.com')

# ==================== LLM 配置 ====================
LLM_MODEL = os.getenv('LLM_MODEL', 'deepseek-chat')
LLM_API_KEY = _require_env('LLM_API_KEY', 'LLM API 密钥')
LLM_BASE_URL = os.getenv('LLM_BASE_URL', 'https://api.deepseek.com')

# ==================== Agent 运行参数（原先散落各处的硬编码）====================
# 数据存储
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data')
DB_PATH = os.path.join(DATA_DIR, 'memory.sqlite')
CHECKPOINT_DB_PATH = os.path.join(DATA_DIR, 'checkpoints.sqlite')
LOG_DIR = os.path.join(DATA_DIR, 'logs')

# 重试与熔断
MAX_TOOL_RETRIES = int(os.getenv('MAX_TOOL_RETRIES', '3'))
CIRCUIT_BREAKER_THRESHOLD = int(os.getenv('CIRCUIT_BREAKER_THRESHOLD', '3'))
CIRCUIT_BREAKER_RECOVERY_SEC = int(os.getenv('CIRCUIT_BREAKER_RECOVERY_SEC', '60'))

# 并行执行
MAX_CONCURRENT_ISSUES = int(os.getenv('MAX_CONCURRENT_ISSUES', '3'))
RATE_LIMIT_PER_MINUTE = int(os.getenv('RATE_LIMIT_PER_MINUTE', '22'))

# Token / 上下文窗口
MAX_STACK_TOKENS = int(os.getenv('MAX_STACK_TOKENS', '1500'))
MAX_CONTEXT_TOKENS = int(os.getenv('MAX_CONTEXT_TOKENS', '4000'))
COMPRESS_THRESHOLD = int(os.getenv('COMPRESS_THRESHOLD', '3000'))

# 搜索与匹配
HISTORY_SEARCH_LIMIT = int(os.getenv('HISTORY_SEARCH_LIMIT', '10'))
HISTORY_MAX_CANDIDATES = int(os.getenv('HISTORY_MAX_CANDIDATES', '5'))
JACCARD_THRESHOLD = float(os.getenv('JACCARD_THRESHOLD', '0.3'))

# API 超时
API_TIMEOUT = int(os.getenv('API_TIMEOUT', '15'))
TREND_API_TIMEOUT = int(os.getenv('TREND_API_TIMEOUT', '30'))

# 版本列表
MAX_VERSIONS_RETURN = int(os.getenv('MAX_VERSIONS_RETURN', '50'))
