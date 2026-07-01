"""音频补抓（headful）：为最新一日 manifest 中缺音频的新闻补抓 HLS 音频。

音频经播放器触发，headless 下播放器不发流，故音频用 headful 单独补抓：
  - 日常 run_crawl 在 headless 下跑（文字+图片，可定时无窗口）；
  - 需要音频时（或定时另设一个 headful 任务）运行本命令补齐。

运行：python -m nhk_tool.fetch_audio
"""
from __future__ import annotations

import json
import sys

from . import config, fetcher, storage
from .logger import CrawlError, DownloadError, NetworkError, get_logger

logger = get_logger()


def main() -> int:
    logger.info("=== 音频补抓（headful）===")
    found = storage.latest_manifest()
    if found is None:
        logger.error("无 manifest，请先运行 run_crawl")
        return 1
    manifest_path, data = found
    day = data.get("date")
    items = data.get("items", [])
    if not items:
        logger.error("manifest 无新闻条目")
        return 1

    updated = 0
    try:
        with fetcher.open_session(headless=False):  # 音频必须 headful
            for it in items:
                news_id = it.get("news_id")
                if it.get("audio"):
                    continue  # 已有音频，跳过
                detail_url = config.DETAIL_URL_TEMPLATE.format(news_id=news_id)
                dest = storage.audio_path(news_id, day)
                try:
                    fetcher.download_audio(detail_url, dest)
                    it["audio"] = f"{news_id}/{config.AUDIO_FILENAME}"
                    updated += 1
                    logger.info("补抓音频成功：%s", news_id)
                except DownloadError as exc:
                    logger.warning("补抓音频失败 %s：%s", news_id, exc)
    except (NetworkError, CrawlError) as exc:
        logger.error("会话失败：%s", exc)
        return 1

    if updated:
        # 回写 manifest（保留原有 has_update 等顶层字段）
        manifest_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info("=== 完成：补抓 %d 条音频，已更新 manifest ===", updated)
    else:
        logger.info("=== 完成：无需补抓（音频已齐或均失败）===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
