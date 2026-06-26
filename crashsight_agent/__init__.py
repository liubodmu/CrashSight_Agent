"""CrashSight Analysis Agent - 自然语言驱动的崩溃分析工具"""
import logging
import os

__version__ = "0.1.0"

# 配置 Python logging 基础设施
# 日志级别通过环境变量 LOG_LEVEL 控制（默认 INFO）
_log_level = os.getenv('LOG_LEVEL', 'INFO').upper()
logging.basicConfig(
    level=getattr(logging, _log_level, logging.INFO),
    format='[%(levelname)s] %(name)s: %(message)s',
    datefmt='%H:%M:%S',
)
