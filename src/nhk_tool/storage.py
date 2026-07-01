"""存储层：固定目录结构 + manifest.json 索引。

目录格式：data/news/{YYYY-MM-DD}/{news_id}/
  image.jpg / audio.aac / body.txt / meta.json
每日根目录写 manifest.json，作为挂件唯一数据契约。同日重复运行覆盖更新。
SAVE_ROOT 下另有 state.json（抓取状态，用于“当日新增”判定）。
"""
from __future__ import annotations

import json
import os
import re
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from . import config
from .logger import get_logger
from .parser import NewsItem

logger = get_logger()

# 日期目录名格式：YYYY-MM-DD
_DAY_DIR_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def day_dir(day: str | None = None) -> Path:
    """返回某日数据目录（默认今天），并确保存在。"""
    day = day or date.today().isoformat()
    d = config.SAVE_ROOT / day
    d.mkdir(parents=True, exist_ok=True)
    return d


def news_dir(news_id: str, day: str | None = None) -> Path:
    """返回单条新闻目录，并确保存在。"""
    d = day_dir(day) / news_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_body(news_id: str, body: str, day: str | None = None) -> Path:
    """保存正文文本，返回文件路径。"""
    path = news_dir(news_id, day) / config.BODY_FILENAME
    path.write_text(body, encoding="utf-8")
    return path


def save_meta(item: NewsItem, day: str | None = None) -> Path:
    """保存单条新闻元数据 meta.json。"""
    path = news_dir(item.news_id, day) / config.META_FILENAME
    path.write_text(
        json.dumps(item.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def image_path(news_id: str, day: str | None = None) -> Path:
    return news_dir(news_id, day) / config.IMAGE_FILENAME


def audio_path(news_id: str, day: str | None = None) -> Path:
    return news_dir(news_id, day) / config.AUDIO_FILENAME


def write_manifest(
    entries: list[dict[str, Any]],
    day: str | None = None,
    has_update: bool = True,
) -> Path:
    """写入当日 manifest.json。

    entries 为有序列表，每项含 news_id/title/publish_time 及各文件相对路径
    （相对当日目录），挂件据此加载本地资源。
    has_update 表示本次相对上次抓取是否有新增新闻，供挂件显示提示条。
    """
    day = day or date.today().isoformat()
    manifest = {
        "date": day,
        "count": len(entries),
        "has_update": has_update,
        "items": entries,
    }
    path = day_dir(day) / config.MANIFEST_FILENAME
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def build_manifest_entry(
    item: NewsItem,
    has_image: bool,
    has_audio: bool,
) -> dict[str, Any]:
    """构造单条 manifest 记录，路径相对当日目录。"""
    return {
        "news_id": item.news_id,
        "title": item.title,
        "publish_time": item.publish_time,
        "body": f"{item.news_id}/{config.BODY_FILENAME}",
        "image": f"{item.news_id}/{config.IMAGE_FILENAME}" if has_image else None,
        "audio": f"{item.news_id}/{config.AUDIO_FILENAME}" if has_audio else None,
    }


def latest_manifest() -> tuple[Path, dict[str, Any]] | None:
    """查找最新一日的 manifest.json，供挂件加载。无数据返回 None。"""
    if not config.SAVE_ROOT.exists():
        return None
    day_dirs = sorted(
        (p for p in config.SAVE_ROOT.iterdir() if p.is_dir()),
        reverse=True,
    )
    for d in day_dirs:
        mf = d / config.MANIFEST_FILENAME
        if mf.exists():
            try:
                data = json.loads(mf.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            return mf, data
    return None


# ---------------------------------------------------------------------------
# 抓取状态（“当日新增”判定基准）
# ---------------------------------------------------------------------------
def state_path() -> Path:
    """状态文件路径：SAVE_ROOT/state.json。"""
    config.SAVE_ROOT.mkdir(parents=True, exist_ok=True)
    return config.SAVE_ROOT / config.STATE_FILENAME


def load_state() -> dict[str, Any]:
    """读取抓取状态；缺失或损坏时返回默认 {'last_seen_ids': []}。"""
    p = config.SAVE_ROOT / config.STATE_FILENAME
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"last_seen_ids": []}


def save_state(
    seen_ids: list[str],
    run_at: str | None = None,
    day: str | None = None,
) -> Path:
    """原子写入抓取状态（临时文件 + os.replace，避免写一半损坏）。"""
    data = {
        "last_seen_ids": list(seen_ids),
        "last_run_at": run_at or datetime.now().isoformat(timespec="seconds"),
        "last_run_day": day or date.today().isoformat(),
    }
    path = state_path()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(tmp, path)
    return path


def detect_new(prev_ids: set[str], current_ids: list[str]) -> tuple[list[bool], bool]:
    """对比上次所见 id，返回 (每条是否新增的布尔列表, 是否有任一新增)。

    纯函数，便于离线测试。首次运行 prev_ids 为空 → 全部视为新增。
    """
    flags = [cid not in prev_ids for cid in current_ids]
    return flags, any(flags)


# ---------------------------------------------------------------------------
# 旧数据清理
# ---------------------------------------------------------------------------
def cleanup_old_days(
    retention_days: int | None = None,
    today: date | None = None,
) -> list[str]:
    """删除 SAVE_ROOT 下早于保留期的日期目录，返回被删目录名列表。

    安全三重防护：仅处理直接子目录、目录名须严格匹配 YYYY-MM-DD、能解析为
    合法日期；任一不满足则跳过（绝不误删 state.json 等非日期项）。
    边界用 < cutoff（恰好等于保留期当天保留）。删除失败记 warning 不中断。
    """
    retention_days = retention_days if retention_days is not None else config.RETENTION_DAYS
    today = today or date.today()
    cutoff = today - timedelta(days=retention_days)

    if not config.SAVE_ROOT.exists():
        return []

    removed: list[str] = []
    for p in config.SAVE_ROOT.iterdir():
        if not p.is_dir():
            continue
        name = p.name
        if not _DAY_DIR_RE.fullmatch(name):
            continue
        try:
            d = datetime.strptime(name, "%Y-%m-%d").date()
        except ValueError:
            continue  # 命名像日期但非法（如 2026-13-99），跳过
        if d < cutoff:
            try:
                shutil.rmtree(p)
                removed.append(name)
            except OSError as exc:
                logger.warning("清理过期目录失败 %s：%s", name, exc)
    return removed
