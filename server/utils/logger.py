import logging

from server.config import get_settings


def configure_logging() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    # httpx 的 INFO 日志会包含完整分享链接和临时访问参数。
    logging.getLogger("httpx").setLevel(logging.WARNING)
