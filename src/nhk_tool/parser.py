"""解析层：站点结构假设全部集中于此。

站点改版时只需修改本文件。关键字段缺失时抛 StructureChangedError。
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

from bs4 import BeautifulSoup

from . import config
from .logger import StructureChangedError


@dataclass
class NewsItem:
    """一条新闻的元数据（资源 URL 为绝对地址）。"""

    news_id: str
    title: str
    publish_time: str          # 发布时间字符串（站点原值）
    image_url: str | None      # 图片绝对 URL，可能缺失
    audio_url: str | None      # 可直接下载的音频绝对 URL；HLS 加密音频时为 None
    detail_url: str            # 详情页 URL
    audio_player_url: str | None = None  # 音频播放器页（带 voiceId），参考用
    voice_uri: str | None = None         # 站点原始语音文件名（如 xxx.m4a）

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _asset_url(news_id: str, name: str | None) -> str | None:
    """把资源文件名拼成绝对 URL；已是 http(s) 的原样返回。"""
    if not name:
        return None
    if name.startswith("http://") or name.startswith("https://"):
        return name
    base = config.ASSET_BASE_TEMPLATE.format(news_id=news_id)
    return base + name


def parse_news_list(data: Any) -> list[NewsItem]:
    """解析 top-list.json，返回当日展示的前 NEWS_COUNT 条。

    现行结构（实测）：顶层为 JSON 数组，每项形如
        {
          "top_priority_number": 1,        # 展示顺序（升序，1 在最前）
          "news_id": "ne2026062513003",
          "top_display_flag": true,        # 是否在主页展示
          "news_prearranged_time": "2026-06-25 20:10:00",
          "title": "...",
          "title_with_ruby": "<ruby>...",
          "news_web_image_uri": "https://.../xxx.jpg",   # 完整 URL
          "news_easy_image_uri": "",                      # 可能为空
          "news_easy_voice_uri": "xxx.m4a",               # 语音文件名
          "has_news_web_image": true, ...
        }

    取 top_display_flag 为真者，按 top_priority_number 升序，截取前 NEWS_COUNT 条。
    关键字段缺失时抛 StructureChangedError。
    """
    if not isinstance(data, list):
        raise StructureChangedError(
            f"top-list.json 顶层应为数组，实际：{type(data).__name__}"
        )
    if not data:
        raise StructureChangedError("top-list.json 顶层数组为空")

    # 仅保留 dict 且标记为主页展示的条目；缺失 flag 时按展示处理（向前兼容）
    displayed = [
        x for x in data
        if isinstance(x, dict) and x.get(config.TOP_DISPLAY_FLAG_FIELD, True)
    ]
    if not displayed:
        raise StructureChangedError("top-list.json 无 top_display_flag 为真的条目")

    # 按展示优先级升序；无该字段的排到末尾
    def _prio(x: dict[str, Any]) -> tuple[int, Any]:
        v = x.get(config.TOP_PRIORITY_FIELD)
        return (0, v) if isinstance(v, int) else (1, 0)

    displayed.sort(key=_prio)

    result: list[NewsItem] = []
    for raw in displayed[: config.NEWS_COUNT]:
        news_id = raw.get("news_id")
        title = raw.get("title") or raw.get("title_with_ruby")
        if not news_id or not title:
            raise StructureChangedError(
                f"新闻条目缺少 news_id/title：keys={list(raw.keys())}"
            )

        # 图片：优先 easy 专用图，退化为 web 图；二者皆可能是完整 URL 或文件名
        image_name = raw.get("news_easy_image_uri") or raw.get("news_web_image_uri")
        audio_name = raw.get("news_easy_voice_uri")
        publish_time = raw.get("news_prearranged_time") or ""

        # 音频说明：现行站点音频经 media.vd.st.nhk 的 AES-128 加密 HLS 流分发，
        # 真实分片 URL 由播放器运行时携带 Akamai token 获取，列表 JSON 里只有
        # 文件名（voice_uri）。故此处 audio_url 置 None，实际音频由 fetcher 在
        # 浏览器会话中捕获带 token 的 HLS 流下载解密（见 fetcher.download_audio）。
        # 这里保留 voice_uri 与播放器页 URL 供参考/兜底。
        audio_player_url = (
            config.AUDIO_PLAYER_URL_TEMPLATE.format(voice_uri=audio_name)
            if audio_name else None
        )

        result.append(
            NewsItem(
                news_id=str(news_id),
                title=str(title),
                publish_time=str(publish_time),
                image_url=_asset_url(str(news_id), image_name or None),
                audio_url=None,
                detail_url=config.DETAIL_URL_TEMPLATE.format(news_id=news_id),
                audio_player_url=audio_player_url,
                voice_uri=audio_name or None,
            )
        )
    return result


def parse_detail_body(html: str) -> str:
    """从详情页 HTML 提取正文纯文本。

    已知正文容器 id 为 #js-article-body（站点改版时改这里）。
    正文里每个分词被包进独立 <span>，故按 <p> 段落聚合：段内文本连续拼接
    （不在 span 间插入分隔符），段落之间用换行分隔，得到自然的日语句子。
    找不到容器时抛 StructureChangedError。
    """
    soup = BeautifulSoup(html, "lxml")

    # 主选择器 + 兜底选择器
    node = (
        soup.select_one("#js-article-body")
        or soup.select_one("div.article-main__body")
        or soup.select_one("article")
    )
    if node is None:
        raise StructureChangedError("详情页未找到正文容器，疑似站点改版")

    # 去除 ruby 注音的读音部分（<rt> 假名、<rp> 括号），只保留汉字原文，
    # 避免提取出“漢字よみがな”混排。显式处理，不依赖 parser 隐式行为。
    for tag in node.find_all(["rt", "rp"]):
        tag.decompose()

    # 优先按段落 <p> 聚合：段内连续、段间换行
    paragraphs = node.find_all("p")
    if paragraphs:
        lines = []
        for p in paragraphs:
            # separator="" 让段内各 span 文本直接相连，保留日语原貌
            t = p.get_text(separator="", strip=True)
            if t:
                lines.append(t)
        text = "\n".join(lines)
    else:
        # 无 <p> 时退化为整体提取
        text = node.get_text(separator="", strip=True)

    if not text:
        raise StructureChangedError("详情页正文容器为空")
    return text
