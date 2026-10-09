import logging
import sys
from pathlib import Path
from logging.handlers import RotatingFileHandler

# 使用 pathlib 创建目录
log_path = Path('logs/media_parser.log')

# 配置日志格式
LOG_FORMAT = '%(asctime)s - %(levelname)s - %(name)s:%(lineno)d - %(message)s'

handlers = [logging.StreamHandler(sys.stdout)]

try:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        str(log_path),
        maxBytes=10 * 1024 * 1024,  # 每个日志文件最大 10MB
        backupCount=5,              # 保留 5 个备份文件
        encoding='utf-8'
    )
    handlers.append(file_handler)
except (PermissionError, OSError) as e:
    sys.stderr.write(f"[Warning] 日志文件无写入权限，已降级为仅控制台日志输出: {e}\n")

logging.basicConfig(
    level=logging.INFO,
    format=LOG_FORMAT,
    handlers=handlers
)

def get_logger(name: str) -> logging.Logger:
    """
    获取项目命名空间下的模块 logger。
    推荐用法：在每个模块开头使用
    from configs.logging_config import get_logger
    logger = get_logger(__name__)
    """
    return logging.getLogger(f'media_parser.{name}')
