"""后端 JSON 命令行桥接：供 Tauri(Rust) 壳通过 sidecar 调用 Python 后端。

设计要点：
- **stdout 只输出一行 JSON**（供 Rust 解析），所有日志/诊断走 stderr 或文件，
  绝不污染 stdout。
- 子命令覆盖：新闻数据(status/read-manifest)、爬取(crawl)、任务计划
  (task-status/task-register/task-unregister)、偏好(prefs-get/prefs-set)。
- 任务计划注册在本进程(sidecar 爬虫 exe)内执行 task_scheduler.register()，
  此时 sys.executable 即爬虫 exe，Action 自然指向它自己 --crawl，无需额外改
  _target_and_args。
- 退出码：0 成功；非 0 表示该命令失败（爬取沿用 run_crawl 的 0/1/2）。

调用方式（由 app.py 的 `backend` 分支转入）：
    NHKEasyNews.exe backend <subcommand> [args...]
    python widget/app.py backend <subcommand> [args...]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from nhk_tool import config, storage

import prefs
import task_scheduler

import re as _re
_DAY_RE = _re.compile(r"^\d{4}-\d{2}-\d{2}$")  # 日期目录名 YYYY-MM-DD


def _emit(obj: Any) -> None:
    """把结果对象作为单行 JSON 写到 stdout（ensure_ascii 避免编码坑）。"""
    sys.stdout.write(json.dumps(obj, ensure_ascii=False))
    sys.stdout.write("\n")
    sys.stdout.flush()


def _data_root() -> str:
    return str(config.DATA_ROOT)


# ---------------------------------------------------------------------------
# 子命令实现
# ---------------------------------------------------------------------------
def _cmd_read_manifest(args: list[str]) -> int:
    """返回 manifest 内容 + 当日目录绝对路径，供前端解析资源路径。

    可选 `--day YYYY-MM-DD` 读取指定历史日期；缺省读最新一日。
    """
    day = _parse_day_arg(args)
    if day:
        found = _read_day_manifest(day)
    else:
        found = storage.latest_manifest()
    if found is None:
        _emit({
            "ok": True, "manifest": None, "day_dir": None,
            "data_root": _data_root(), "requested_day": day,
        })
        return 0
    manifest_path, data = found
    _emit({
        "ok": True,
        "manifest": data,
        "day_dir": str(manifest_path.parent),
        "manifest_path": str(manifest_path),
        "data_root": _data_root(),
        "requested_day": day,
    })
    return 0


def _cmd_list_days() -> int:
    """列出本地已存档的日期（有 manifest.json 的日期目录），降序。

    另返回 today 供前端判断「是否为当天」（仅当天允许爬取）。
    """
    from datetime import date
    days: list[str] = []
    if config.SAVE_ROOT.exists():
        for p in sorted(config.SAVE_ROOT.iterdir(), reverse=True):
            if p.is_dir() and (p / config.MANIFEST_FILENAME).exists():
                if _DAY_RE.match(p.name):
                    days.append(p.name)
    _emit({"ok": True, "days": days, "today": date.today().isoformat()})
    return 0


def _read_day_manifest(day: str):
    """读取指定日期目录的 manifest，返回 (path, data) 或 None。"""
    import json as _json
    mf = config.SAVE_ROOT / day / config.MANIFEST_FILENAME
    if not mf.exists():
        return None
    try:
        data = _json.loads(mf.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return mf, data


def _parse_day_arg(args: list[str]) -> str | None:
    """从 args 解析 `--day YYYY-MM-DD`，非法/缺省返回 None。"""
    for i, a in enumerate(args):
        if a == "--day" and i + 1 < len(args):
            v = args[i + 1].strip()
            return v if _DAY_RE.match(v) else None
    return None


def _cmd_status() -> int:
    """综合状态：数据根 + 最新 manifest 摘要 + 任务计划状态 + 偏好。"""
    found = storage.latest_manifest()
    manifest_summary = None
    day_dir = None
    if found is not None:
        manifest_path, data = found
        day_dir = str(manifest_path.parent)
        manifest_summary = {
            "date": data.get("date"),
            "count": data.get("count"),
            "has_update": data.get("has_update"),
        }
    _emit({
        "ok": True,
        "data_root": _data_root(),
        "day_dir": day_dir,
        "manifest": manifest_summary,
        "task": task_scheduler.get_status(),
        "prefs": prefs.load_prefs(),
    })
    return 0


def _cmd_crawl() -> int:
    """同步触发一次爬取（沿用 run_crawl 的退出码 0/1/2）。"""
    import os
    # 挂件手动爬取（backend crawl 仅由 Tauri 爬取按钮调用）
    os.environ.setdefault("NHK_CRAWL_SOURCE", "manual")
    from nhk_tool import run_crawl
    code = run_crawl.main()
    _emit({"ok": code == 0, "exit_code": code})
    return code


def _cmd_task_status() -> int:
    _emit({"ok": True, "task": task_scheduler.get_status()})
    return 0


def _cmd_task_register(args: list[str]) -> int:
    """注册/更新任务计划。times 以空格分隔的 HH:MM 传入（--times 之后）。

    弹 UAC（在本 sidecar 进程内提权）；完成后以 get_status() 复核系统真相。
    """
    times = _parse_times(args)
    task_scheduler.register(times)
    st = task_scheduler.get_status()
    registered = bool(st.get("registered"))
    # 以系统真相回写偏好
    prefs.set_task_enabled(registered)
    _emit({"ok": registered, "task": st})
    return 0 if registered else 1


def _cmd_task_unregister() -> int:
    task_scheduler.unregister()
    st = task_scheduler.get_status()
    registered = bool(st.get("registered"))
    prefs.set_task_enabled(registered)
    # 注销成功 = 不再注册
    _emit({"ok": not registered, "task": st})
    return 0 if not registered else 1


def _cmd_prefs_get() -> int:
    _emit({"ok": True, "prefs": prefs.load_prefs()})
    return 0


def _cmd_prefs_set(args: list[str]) -> int:
    """合并写入偏好。参数为单个 JSON 对象字符串（仅合并已知键，校验交给 prefs）。"""
    if not args:
        _emit({"ok": False, "error": "缺少 JSON 参数"})
        return 1
    try:
        patch = json.loads(args[0])
    except (ValueError, IndexError) as exc:
        _emit({"ok": False, "error": f"JSON 解析失败：{exc}"})
        return 1
    if not isinstance(patch, dict):
        _emit({"ok": False, "error": "参数须为 JSON 对象"})
        return 1

    # 逐键走 prefs 的校验 setter，未知键忽略
    if "close_action" in patch:
        prefs.set_close_action(patch["close_action"])
    if "autostart" in patch:
        prefs.set_autostart(bool(patch["autostart"]))
    if "crawl_times" in patch and isinstance(patch["crawl_times"], list):
        prefs.set_crawl_times([str(t) for t in patch["crawl_times"]])
    if "task_enabled" in patch:
        prefs.set_task_enabled(bool(patch["task_enabled"]))

    _emit({"ok": True, "prefs": prefs.load_prefs()})
    return 0


def _parse_times(args: list[str]) -> list[str]:
    """从 args 中解析时间列表：支持 `--times 09:00 21:00` 或直接位置参数。"""
    times: list[str] = []
    skip_flag = False
    for a in args:
        if a == "--times":
            skip_flag = True
            continue
        times.append(a)
    # prefs 缺省时回退默认；这里把空列表交给 task_scheduler._norm_times 兜底
    if not times:
        times = prefs.get_crawl_times()
    return times


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
_USAGE = (
    "用法：backend <command> [args]\n"
    "  status                 综合状态(数据/任务/偏好)\n"
    "  read-manifest [--day YYYY-MM-DD]  manifest + 目录(缺省最新一日)\n"
    "  list-days              列出本地已存档日期 + today\n"
    "  crawl                  触发一次爬取\n"
    "  task-status            任务计划状态\n"
    "  task-register [--times HH:MM ...]  注册/更新任务(弹 UAC)\n"
    "  task-unregister        注销任务(弹 UAC)\n"
    "  prefs-get              读取偏好\n"
    "  prefs-set <json>       合并写入偏好\n"
)


def main(argv: list[str]) -> int:
    """backend 子命令分发。argv 为 `backend` 之后的参数。"""
    if not argv:
        _emit({"ok": False, "error": "缺少子命令", "usage": _USAGE})
        return 1

    cmd, rest = argv[0], argv[1:]
    try:
        if cmd == "status":
            return _cmd_status()
        if cmd == "read-manifest":
            return _cmd_read_manifest(rest)
        if cmd == "list-days":
            return _cmd_list_days()
        if cmd == "crawl":
            return _cmd_crawl()
        if cmd == "task-status":
            return _cmd_task_status()
        if cmd == "task-register":
            return _cmd_task_register(rest)
        if cmd == "task-unregister":
            return _cmd_task_unregister()
        if cmd == "prefs-get":
            return _cmd_prefs_get()
        if cmd == "prefs-set":
            return _cmd_prefs_set(rest)
    except Exception as exc:  # noqa: BLE001  # 兜底：任何异常转为 JSON 错误，便于 Rust 侧处理
        _emit({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        return 1

    _emit({"ok": False, "error": f"未知子命令：{cmd}", "usage": _USAGE})
    return 1
