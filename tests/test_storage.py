"""storage 存储层离线单测，使用临时目录避免污染真实 data/。"""
import json

import pytest

from nhk_tool import config, storage
from nhk_tool.parser import NewsItem


@pytest.fixture
def tmp_save_root(tmp_path, monkeypatch):
    """把 SAVE_ROOT 指向临时目录。"""
    root = tmp_path / "news"
    monkeypatch.setattr(config, "SAVE_ROOT", root)
    return root


def _item(news_id="k1000", title="タイトル"):
    return NewsItem(
        news_id=news_id,
        title=title,
        publish_time="2026-06-26 11:00:00",
        image_url="https://example/x.jpg",
        audio_url="https://example/x.mp3",
        detail_url="https://example/x.html",
    )


def test_save_body_and_meta(tmp_save_root):
    item = _item()
    body_path = storage.save_body(item.news_id, "本文テキスト", day="2026-06-26")
    meta_path = storage.save_meta(item, day="2026-06-26")

    assert body_path.read_text(encoding="utf-8") == "本文テキスト"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["news_id"] == "k1000"
    assert meta["title"] == "タイトル"


def test_write_and_read_manifest(tmp_save_root):
    item = _item()
    entry = storage.build_manifest_entry(item, has_image=True, has_audio=True)
    storage.write_manifest([entry], day="2026-06-26")

    found = storage.latest_manifest()
    assert found is not None
    _, data = found
    assert data["date"] == "2026-06-26"
    assert data["count"] == 1
    assert data["items"][0]["image"] == "k1000/image.jpg"
    assert data["items"][0]["audio"] == f"k1000/{config.AUDIO_FILENAME}"


def test_manifest_entry_without_assets(tmp_save_root):
    item = _item()
    entry = storage.build_manifest_entry(item, has_image=False, has_audio=False)
    assert entry["image"] is None
    assert entry["audio"] is None
    assert entry["body"] == "k1000/body.txt"


def test_latest_manifest_picks_newest_day(tmp_save_root):
    item = _item()
    e = storage.build_manifest_entry(item, has_image=False, has_audio=False)
    storage.write_manifest([e], day="2026-06-24")
    storage.write_manifest([e], day="2026-06-26")
    storage.write_manifest([e], day="2026-06-25")

    _, data = storage.latest_manifest()
    assert data["date"] == "2026-06-26"


def test_latest_manifest_none_when_empty(tmp_save_root):
    assert storage.latest_manifest() is None


# --------------------------- 状态读写 ---------------------------
def test_load_state_missing_returns_default(tmp_save_root):
    state = storage.load_state()
    assert state == {"last_seen_ids": []}


def test_save_and_load_state_roundtrip(tmp_save_root):
    storage.save_state(["ne1", "ne2"], run_at="2026-06-26T09:30:00", day="2026-06-26")
    state = storage.load_state()
    assert state["last_seen_ids"] == ["ne1", "ne2"]
    assert state["last_run_day"] == "2026-06-26"
    # 不残留临时文件
    assert not (tmp_save_root / (config.STATE_FILENAME + ".tmp")).exists()


def test_load_state_corrupt_returns_default(tmp_save_root):
    tmp_save_root.mkdir(parents=True, exist_ok=True)
    (tmp_save_root / config.STATE_FILENAME).write_text("{ not json", encoding="utf-8")
    assert storage.load_state() == {"last_seen_ids": []}


# --------------------------- 新增判定 ---------------------------
def test_detect_new_first_run_all_new():
    flags, has_update = storage.detect_new(set(), ["a", "b", "c"])
    assert flags == [True, True, True]
    assert has_update is True


def test_detect_new_all_old():
    flags, has_update = storage.detect_new({"a", "b"}, ["a", "b"])
    assert flags == [False, False]
    assert has_update is False


def test_detect_new_partial():
    flags, has_update = storage.detect_new({"a"}, ["a", "x"])
    assert flags == [False, True]
    assert has_update is True


# --------------------------- 旧数据清理 ---------------------------
def _make_day(root, name):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.json").write_text("{}", encoding="utf-8")
    return d


def test_cleanup_removes_only_expired(tmp_save_root):
    from datetime import date

    tmp_save_root.mkdir(parents=True, exist_ok=True)
    _make_day(tmp_save_root, "2026-05-01")   # 过期（>30天）
    _make_day(tmp_save_root, "2026-06-20")   # 近期保留
    _make_day(tmp_save_root, "2026-06-26")   # 当天保留
    # 非日期项不应被删
    (tmp_save_root / config.STATE_FILENAME).write_text("{}", encoding="utf-8")
    _make_day(tmp_save_root, "backup")        # 非日期命名
    _make_day(tmp_save_root, "2026-13-99")    # 像日期但非法

    removed = storage.cleanup_old_days(30, today=date(2026, 6, 26))

    assert removed == ["2026-05-01"]
    assert not (tmp_save_root / "2026-05-01").exists()
    assert (tmp_save_root / "2026-06-20").exists()
    assert (tmp_save_root / "2026-06-26").exists()
    assert (tmp_save_root / config.STATE_FILENAME).exists()
    assert (tmp_save_root / "backup").exists()
    assert (tmp_save_root / "2026-13-99").exists()


def test_cleanup_boundary_keeps_cutoff_day(tmp_save_root):
    from datetime import date

    tmp_save_root.mkdir(parents=True, exist_ok=True)
    # cutoff = 2026-06-26 - 30 = 2026-05-27；该天用 < 比较应保留
    _make_day(tmp_save_root, "2026-05-27")
    _make_day(tmp_save_root, "2026-05-26")   # 早一天应删
    removed = storage.cleanup_old_days(30, today=date(2026, 6, 26))
    assert removed == ["2026-05-26"]
    assert (tmp_save_root / "2026-05-27").exists()


def test_cleanup_empty_root_returns_empty(tmp_save_root):
    from datetime import date

    assert storage.cleanup_old_days(30, today=date(2026, 6, 26)) == []
