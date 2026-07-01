"""parser 解析层离线单测。"""
import json
from pathlib import Path

import pytest

from nhk_tool import config
from nhk_tool.parser import parse_news_list, parse_detail_body
from nhk_tool.logger import StructureChangedError

FIXTURES = Path(__file__).parent / "fixtures"


def _load_list():
    return json.loads((FIXTURES / "top-list.json").read_text(encoding="utf-8"))


def test_parse_news_list_returns_news_count():
    items = parse_news_list(_load_list())
    # 只取 NEWS_COUNT 条
    assert len(items) == config.NEWS_COUNT


def test_parse_news_list_orders_by_priority():
    items = parse_news_list(_load_list())
    # 按 top_priority_number 升序：第一条 priority=1
    assert items[0].news_id == "ne2026062513003"
    assert items[1].news_id == "ne2026062514455"


def test_parse_news_list_builds_urls():
    item = parse_news_list(_load_list())[0]
    # 详情页：/news/easy/{news_id}/{news_id}.html
    assert item.detail_url.endswith("ne2026062513003/ne2026062513003.html")
    # 图片为完整 URL（news_web_image_uri 已是绝对地址，原样保留）
    assert item.image_url.startswith("https://")
    # 音频海外受限：audio_url 置 None，但保留播放器页与原始文件名
    assert item.audio_url is None
    assert item.voice_uri.endswith(".m4a")
    assert "voiceId=" in item.audio_player_url


def test_parse_news_list_filters_top_display_flag():
    data = [
        {"top_priority_number": 2, "news_id": "ne2", "title": "展示2",
         "top_display_flag": True},
        {"top_priority_number": 1, "news_id": "ne_hidden", "title": "隐藏",
         "top_display_flag": False},
        {"top_priority_number": 3, "news_id": "ne3", "title": "展示3",
         "top_display_flag": True},
    ]
    items = parse_news_list(data)
    ids = [i.news_id for i in items]
    assert "ne_hidden" not in ids
    # 仅展示项，按 priority 升序
    assert ids == ["ne2", "ne3"]


def test_parse_news_list_empty_raises():
    with pytest.raises(StructureChangedError):
        parse_news_list([])


def test_parse_news_list_wrong_toplevel_raises():
    with pytest.raises(StructureChangedError):
        parse_news_list({"unexpected": "dict"})


def test_parse_news_list_missing_fields_raises():
    bad = [{"top_priority_number": 1, "top_display_flag": True}]  # 缺 news_id/title
    with pytest.raises(StructureChangedError):
        parse_news_list(bad)


def test_parse_detail_body_extracts_text():
    html = (FIXTURES / "detail.html").read_text(encoding="utf-8")
    text = parse_detail_body(html)
    assert "本文" in text
    assert "ヘッダー" not in text  # 只取正文容器
    assert "フッター" not in text


def test_parse_detail_body_strips_ruby_and_joins_words():
    """正文应剥离 ruby 读音、段内连续、段间换行，不出现分词空格。"""
    html = (FIXTURES / "detail.html").read_text(encoding="utf-8")
    text = parse_detail_body(html)
    # 段内连续，无分词空格
    assert text.startswith("これは本文です。")
    # ruby 读音（<rt> 假名）不混入正文
    assert "ほんぶん" not in text
    assert "だんらく" not in text
    # 段落之间用换行分隔
    assert "\n" in text
    assert text.split("\n")[1].startswith("2つ目の段落")


def test_parse_detail_body_missing_container_raises():
    with pytest.raises(StructureChangedError):
        parse_detail_body("<html><body><p>no container</p></body></html>")
