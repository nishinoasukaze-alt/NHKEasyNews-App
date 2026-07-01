"""爬取入口：编排 fetch -> parse -> storage，写当日 manifest。

单条新闻失败不中断整体，最后汇总成功/失败条数。
作为模块运行：python -m nhk_tool.run_crawl
"""
from __future__ import annotations

import sys
from datetime import date

from . import config, fetcher, parser, storage
from .logger import (
    CrawlError,
    DownloadError,
    NetworkError,
    StructureChangedError,
    get_logger,
)

logger = get_logger()


def crawl_one(item: parser.NewsItem, day: str) -> dict | None:
    """处理单条新闻：下载资源、抓正文、写文件，返回 manifest 记录。

    失败记录日志并返回 None（不中断整体）。
    """
    try:
        # 正文
        html = fetcher.fetch_detail(item.detail_url)
        body = parser.parse_detail_body(html)
        storage.save_body(item.news_id, body, day)

        # 图片（缺失或失败不致命）
        has_image = False
        if item.image_url:
            try:
                fetcher.download_file(item.image_url, storage.image_path(item.news_id, day))
                has_image = True
            except DownloadError as exc:
                logger.warning("图片下载失败 %s：%s", item.news_id, exc)

        # 音频（加密 HLS，缺失或失败不致命）
        # 新版 headless（--headless=new）下播放器 iframe 正常发流，可直接抓。
        has_audio = False
        audio_dest = storage.audio_path(item.news_id, day)
        if config.FETCH_AUDIO and item.voice_uri:
            try:
                fetcher.download_audio(item.detail_url, audio_dest)
                has_audio = True
            except DownloadError as exc:
                logger.warning("音频下载失败 %s：%s", item.news_id, exc)

        storage.save_meta(item, day)
        logger.info("完成新闻 %s：%s", item.news_id, item.title)
        return storage.build_manifest_entry(item, has_image, has_audio)

    except StructureChangedError as exc:
        logger.error("结构异常，跳过 %s：%s", item.news_id, exc)
    except (NetworkError, CrawlError) as exc:
        logger.error("抓取失败，跳过 %s：%s", item.news_id, exc)
    return None


def main() -> int:
    day = date.today().isoformat()
    # 触发来源：由环境变量 NHK_CRAWL_SOURCE 传入（scheduled=定时任务 / manual=挂件手动
    # / cli=命令行直跑），显式写入日志便于区分谁触发了本次爬取。
    import os
    source = os.environ.get("NHK_CRAWL_SOURCE", "cli")
    source_label = {
        "scheduled": "Windows 定时任务",
        "manual": "挂件手动爬取",
        "cli": "命令行",
    }.get(source, source)
    logger.info("=== 开始爬取 NHK Easy News（%s）｜触发来源：%s ===", day, source_label)

    try:
        with fetcher.open_session():
            try:
                raw = fetcher.fetch_news_list()
                items = parser.parse_news_list(raw)
            except StructureChangedError as exc:
                logger.error("新闻列表结构异常，终止：%s", exc)
                return 2
            except NetworkError as exc:
                logger.error(
                    "获取新闻列表失败，终止：%s；"
                    "若为同意 cookie 失效，请运行：python -m nhk_tool.setup_consent",
                    exc,
                )
                return 1

            entries = []
            for item in items:
                entry = crawl_one(item, day)
                if entry:
                    entries.append(entry)
    except NetworkError as exc:
        # 浏览器启动/会话级失败
        logger.error("浏览器会话失败，终止：%s", exc)
        return 1

    if not entries:
        logger.error("没有任何新闻成功抓取，不写 manifest")
        return 1

    # “当日新增”判定：与上次成功抓取所见 news_id 集合对比
    prev_ids = set(storage.load_state().get("last_seen_ids", []))
    current_ids = [e["news_id"] for e in entries]
    flags, has_update = storage.detect_new(prev_ids, current_ids)
    for entry, is_new in zip(entries, flags):
        entry["is_new"] = is_new

    manifest_path = storage.write_manifest(entries, day, has_update=has_update)

    # manifest 成功写盘后才更新状态（失败/0 条不更新，保持对比基准）
    storage.save_state(current_ids, day=day)

    new_count = sum(flags)
    if has_update:
        logger.info("本次新增 %d 条新闻", new_count)
    else:
        logger.info("当日新闻暂未更新（最新四条均为已见过的）")

    # 清理过期日期目录（保留约 RETENTION_DAYS 天）
    try:
        removed = storage.cleanup_old_days()
        if removed:
            logger.info("清理过期目录 %d 个：%s", len(removed), ", ".join(removed))
    except OSError as exc:
        logger.warning("清理过期目录时出错（不影响本次结果）：%s", exc)

    logger.info(
        "=== 完成：成功 %d / 共 %d 条，has_update=%s，manifest=%s ===",
        len(entries), len(items), has_update, manifest_path,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
