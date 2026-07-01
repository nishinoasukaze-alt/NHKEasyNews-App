"""挂件本地偏好读写（与爬虫数据分离）。

存于 data/widget_prefs.json（打包后在 %LOCALAPPDATA%\\NHKEasyNews）：
    {
      "close_action": "tray" | "quit" | null,   # 关闭行为，null=每次询问
      "autostart": true | false,                  # 开机自启
      "crawl_times": ["09:00", "21:00"],          # 每日爬取时间段（HH:MM）
      "task_enabled": true | false                # 是否启用 Windows 任务计划定时
    }
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from nhk_tool import config

_PREFS_PATH = config.DATA_ROOT / "widget_prefs.json"
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")  # HH:MM 24 小时


def load_prefs() -> dict[str, Any]:
    try:
        return json.loads(_PREFS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_prefs(prefs: dict[str, Any]) -> None:
    _PREFS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _PREFS_PATH.write_text(
        json.dumps(prefs, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def get_close_action() -> str | None:
    """返回已记住的关闭行为：'tray' / 'quit' / None（未记住）。"""
    val = load_prefs().get("close_action")
    return val if val in ("tray", "quit") else None


def set_close_action(action: str | None) -> None:
    """记住关闭行为；传 None 清除（恢复每次询问）。"""
    prefs = load_prefs()
    if action in ("tray", "quit"):
        prefs["close_action"] = action
    else:
        prefs.pop("close_action", None)
    save_prefs(prefs)


def get_autostart() -> bool:
    """是否开机自启（仅偏好状态；实际 .lnk 落地见 autostart 模块）。"""
    return bool(load_prefs().get("autostart", False))


def set_autostart(enabled: bool) -> None:
    prefs = load_prefs()
    prefs["autostart"] = bool(enabled)
    save_prefs(prefs)


def get_task_enabled() -> bool:
    """是否启用 Windows 任务计划定时（仅偏好状态；系统真相见 task_scheduler）。"""
    return bool(load_prefs().get("task_enabled", False))


def set_task_enabled(enabled: bool) -> None:
    prefs = load_prefs()
    prefs["task_enabled"] = bool(enabled)
    save_prefs(prefs)


def _valid_times(times: list[str]) -> list[str]:
    """过滤出合法 HH:MM，去重并按时间排序；空则回退默认。"""
    seen = []
    for t in times:
        t = str(t).strip()
        if _TIME_RE.match(t) and t not in seen:
            seen.append(t)
    seen.sort()
    return seen or list(config.DEFAULT_CRAWL_TIMES)


def get_crawl_times() -> list[str]:
    """返回每日爬取时间段（HH:MM 列表）；缺失/非法回退默认。"""
    raw = load_prefs().get("crawl_times")
    if not isinstance(raw, list):
        return list(config.DEFAULT_CRAWL_TIMES)
    return _valid_times(raw)


def set_crawl_times(times: list[str]) -> list[str]:
    """保存爬取时间段（自动校验/排序/去重）；返回实际保存的值。"""
    valid = _valid_times(list(times))
    prefs = load_prefs()
    prefs["crawl_times"] = valid
    save_prefs(prefs)
    return valid

