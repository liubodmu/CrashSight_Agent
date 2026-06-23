"""项目配置 + 鉴权信息"""
import os
from dotenv import load_dotenv

load_dotenv()

# ==================== CrashSight 项目配置 ====================
PROJECTS = {
    'android_exp': {
        'name': 'UAMobile体验服_Android',
        'aliases': ['安卓体验服', '安卓体验', 'android体验', 'android体验服', '安卓exp'],
        'platform': 1,
        'pid': 1,
        'appId': '1110268141',
        'appKey': 'e86c4f2d-e9e0-425b-a89e-b5f9f9f2f0cf',
        'isExperience': True,
        'prod_counterpart': 'android_prod',
    },
    'android_prod': {
        'name': 'UAMobile正式服_Android',
        'aliases': ['安卓正式服', '安卓正式', 'android正式', 'android正式服', '安卓prod'],
        'platform': 1,
        'pid': 1,
        'appId': '1110196838',
        'appKey': '5334c709-edc0-489b-89ca-70334603538a',
        'isExperience': False,
        'prod_counterpart': None,
    },
    'ios_exp': {
        'name': 'UAMobile体验服_iOS',
        'aliases': ['ios体验服', 'ios体验', '苹果体验', '苹果体验服', 'iOS体验'],
        'platform': 2,
        'pid': 2,
        'appId': 'i1110268141',
        'appKey': '562af1f2-fdf0-49e7-ab86-b9c60ee37c2d',
        'isExperience': True,
        'prod_counterpart': 'ios_prod',
    },
    'ios_prod': {
        'name': 'UAMobile正式服_iOS',
        'aliases': ['ios正式服', 'ios正式', '苹果正式', '苹果正式服', 'iOS正式'],
        'platform': 2,
        'pid': 2,
        'appId': 'i1110196838',
        'appKey': '91f8a7b7-a039-4f15-b76c-b5b151c6d100',
        'isExperience': False,
        'prod_counterpart': None,
    },
    'harmony_exp': {
        'name': 'UAMobile体验版_Harmony',
        'aliases': ['鸿蒙体验', '鸿蒙体验版', '鸿蒙体验服', 'harmony体验', '鸿蒙exp'],
        'platform': 3,
        'pid': 40,
        'appId': 'f8e684f35f',
        'appKey': '9de63179-4354-40ee-b0cf-126a49dcbfc8',
        'isExperience': True,
        'prod_counterpart': 'harmony_prod',
    },
    'harmony_prod': {
        'name': 'UAMobile正式服_Harmony',
        'aliases': ['鸿蒙正式', '鸿蒙正式版', '鸿蒙正式服', 'harmony正式', '鸿蒙prod'],
        'platform': 3,
        'pid': 40,
        'appId': '59caa6f7f5',
        'appKey': 'f0c26807-d1b2-4fe0-8948-66d38cca8f1f',
        'isExperience': False,
        'prod_counterpart': None,
    },
}

# ==================== CrashSight 鉴权 ====================
USER_AUTH = {
    'userId': os.getenv('CS_USER_ID', '43393'),
    'userKey': os.getenv('CS_USER_KEY', '72b7bc22-5b35-43b8-8149-bee29482f5b9'),
}

CRASHSIGHT_BASE = 'https://crashsight.qq.com'

# Cookie 认证（OpenAPI 不支持的接口用）
COOKIE_AUTH = {
    'token-skey': os.getenv('CS_TOKEN_SKEY', ''),
    'token-lifeTime': os.getenv('CS_TOKEN_LIFETIME', ''),
    'crashsight_session_cnprod': os.getenv('CS_SESSION', ''),
    'crashsight_gateway_session': os.getenv('CS_GATEWAY_SESSION', ''),
}

# ==================== TAPD 配置 ====================
TAPD_TOKEN = os.getenv('TAPD_TOKEN', '1c3d2b8bfe9504c10b528d72cf66f9cca8d58d51')
TAPD_API = 'https://apiv2.tapd.woa.com'

# ==================== LLM 配置 ====================
LLM_MODEL = os.getenv('LLM_MODEL', 'deepseek-chat')
LLM_API_KEY = os.getenv('LLM_API_KEY', '')
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
