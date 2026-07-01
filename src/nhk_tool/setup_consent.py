"""一次性同意刷新：用可见（headful）浏览器过「海外アクセス確認」门禁，
保存同意 cookie（storage_state）供后续 headless 爬取复用。

何时运行：
  - 首次使用本工具；
  - 日志提示「同意 cookie 失效」（JWT 过期）时。
平时无需运行——run_crawl 全程 headless 复用 cookie。

运行：python -m nhk_tool.setup_consent
"""
from __future__ import annotations

import sys

from . import fetcher, config
from .logger import CrawlError, NetworkError, get_logger

logger = get_logger()


def main() -> int:
    logger.info("=== 刷新同意 cookie（headful）===")
    try:
        # 强制 headful：过同意弹窗在无头下不可靠
        with fetcher.open_session(headless=False) as sess:
            sess.ensure_consent()
            # ensure_consent 内点击成功会自动保存；这里再确保落盘一次
            sess.save_auth_state()
    except (NetworkError, CrawlError) as exc:
        logger.error("刷新同意 cookie 失败：%s", exc)
        return 1
    logger.info("=== 完成：cookie 已保存至 %s ===", config.AUTH_STATE_PATH)
    return 0


if __name__ == "__main__":
    sys.exit(main())
