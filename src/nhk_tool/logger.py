"""分级日志与分类异常。

错误明确分三类，便于排查站点改版 / 网络 / 下载问题，不静默吞错。
"""
from __future__ import annotations

import logging
import sys
from datetime import date
from pathlib import Path

from . import config


# ---------------------------------------------------------------------------
# 分类异常
# ---------------------------------------------------------------------------
class CrawlError(Exception):
    """爬取相关错误基类。"""


class NetworkError(CrawlError):
    """网络请求失败（超时、连接错误、非 2xx 状态码）。"""


class StructureChangedError(CrawlError):
    """页面/接口结构与预期不符，关键字段解析不到，疑似站点改版。"""


class DownloadError(CrawlError):
    """资源文件（图片/音频）下载失败。"""


# ---------------------------------------------------------------------------
# 日志配置
# ---------------------------------------------------------------------------
_LOGGER_NAME = "nhk_tool"
_configured = False


def get_logger() -> logging.Logger:
    """返回全局 logger，首次调用时配置文件 + 控制台双输出。"""
    global _configured
    logger = logging.getLogger(_LOGGER_NAME)
    if _configured:
        return logger

    logger.setLevel(logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 控制台（Windows 默认 GBK 控制台会让日文/中文乱码，显式切到 UTF-8）。
    # backend JSON 桥接模式（NHK_LOG_STDERR=1）下，控制台日志改走 stderr，
    # 避免污染 stdout 上供 Rust 解析的单行 JSON。
    import os
    log_stream = sys.stderr if os.environ.get("NHK_LOG_STDERR") == "1" else sys.stdout
    try:
        if hasattr(log_stream, "reconfigure"):
            log_stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001  # 重配置失败不影响主流程
        pass
    console = logging.StreamHandler(log_stream)
    console.setFormatter(fmt)
    logger.addHandler(console)

    # 文件：data/logs/crawl-YYYY-MM-DD.log
    try:
        log_dir: Path = config.LOG_ROOT
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / f"crawl-{date.today().isoformat()}.log"
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(fmt)
        logger.addHandler(file_handler)
    except OSError as exc:  # 文件日志失败不应阻断主流程
        logger.warning("无法创建日志文件，仅输出到控制台：%s", exc)

    _configured = True
    return logger
