"""scheduler 唤醒补偿判定与 prefs 设置项的离线单测（不依赖 Qt/网络）。"""
import sys
from datetime import datetime
from pathlib import Path

import pytest

_WIDGET = Path(__file__).resolve().parents[1] / "widget"
_SRC = Path(__file__).resolve().parents[1] / "src"
for p in (str(_WIDGET), str(_SRC)):
    if p not in sys.path:
        sys.path.insert(0, p)


# ---- 唤醒补偿判定（纯函数，单独导入避免拉起 Qt/APScheduler 全量）----
def _missed():
    from scheduler import _missed_catchup_needed
    return _missed_catchup_needed


def test_catchup_no_last_run_and_time_passed():
    assert _missed()(["09:00", "21:00"], None, datetime(2026, 6, 29, 10, 0)) is True


def test_catchup_already_ran_after_schedule():
    assert _missed()(
        ["09:00", "21:00"], "2026-06-29T09:30:00", datetime(2026, 6, 29, 10, 0)
    ) is False


def test_catchup_ran_before_schedule_missed():
    # 8点跑过，10点检查：9点那次错过了 → 需补
    assert _missed()(
        ["09:00", "21:00"], "2026-06-29T08:00:00", datetime(2026, 6, 29, 10, 0)
    ) is True


def test_catchup_before_any_schedule():
    # 早上 8 点，还没到 9 点 → 不补
    assert _missed()(
        ["09:00", "21:00"], "2026-06-28T21:05:00", datetime(2026, 6, 29, 8, 0)
    ) is False


def test_catchup_corrupt_last_run():
    # last_run_at 非法 → 当作没跑过，已过时刻则补
    assert _missed()(["09:00"], "garbage", datetime(2026, 6, 29, 10, 0)) is True


# ---- prefs 时间校验 ----
def test_crawl_times_validation(tmp_path, monkeypatch):
    import prefs
    from nhk_tool import config
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(prefs, "_PREFS_PATH", tmp_path / "widget_prefs.json")

    # 合法：排序去重
    assert prefs.set_crawl_times(["21:00", "09:00", "09:00"]) == ["09:00", "21:00"]
    # 非法格式被过滤，全非法回退默认
    assert prefs.set_crawl_times(["25:99", "abc"]) == list(config.DEFAULT_CRAWL_TIMES)
    # 读取
    prefs.set_crawl_times(["07:30", "19:45"])
    assert prefs.get_crawl_times() == ["07:30", "19:45"]


def test_autostart_pref_roundtrip(tmp_path, monkeypatch):
    import prefs
    from nhk_tool import config
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(prefs, "_PREFS_PATH", tmp_path / "widget_prefs.json")
    assert prefs.get_autostart() is False
    prefs.set_autostart(True)
    assert prefs.get_autostart() is True


def test_task_enabled_pref_roundtrip(tmp_path, monkeypatch):
    import prefs
    from nhk_tool import config
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(prefs, "_PREFS_PATH", tmp_path / "widget_prefs.json")
    assert prefs.get_task_enabled() is False
    prefs.set_task_enabled(True)
    assert prefs.get_task_enabled() is True
    prefs.set_task_enabled(False)
    assert prefs.get_task_enabled() is False


# ---- task_scheduler 纯逻辑（命令构造/时间规范化，不实际调 PowerShell）----
def test_task_norm_times_sort_dedup():
    from task_scheduler import _norm_times
    assert _norm_times(["21:00", "09:00", "09:00"]) == ["09:00", "21:00"]


def test_task_norm_times_filter_invalid_falls_back():
    from task_scheduler import _norm_times
    assert _norm_times(["25:99", "abc", ""]) == ["09:00", "21:00"]


def test_task_ps_quote_doubles_single_quote():
    from task_scheduler import _ps_quote
    assert _ps_quote("it's") == "it''s"
    assert _ps_quote("plain") == "plain"


def test_task_register_script_contains_key_settings():
    from task_scheduler import _build_register_script, TASK_NAME
    script = _build_register_script(["09:00", "21:00"])
    # 关键参数齐全
    assert "-WakeToRun" in script
    assert "-LogonType S4U" in script
    assert TASK_NAME in script
    # 每个时间一个 Daily 触发器
    assert script.count("New-ScheduledTaskTrigger -Daily -At") == 2
    # 幂等：先 Unregister 再 Register
    assert "Unregister-ScheduledTask" in script
    assert "Register-ScheduledTask" in script


def test_task_target_args_has_crawl_flag():
    from task_scheduler import _target_and_args
    execute, argument, workdir = _target_and_args()
    assert "--crawl" in argument
    assert execute  # 非空
    assert workdir


def test_task_action_marks_from_task():
    # 定时任务 Action 需带 --from-task，供 run_crawl 区分触发来源
    from task_scheduler import _target_and_args
    _, argument, _ = _target_and_args()
    assert "--from-task" in argument


# ---- backend_cli 纯逻辑（命令分发/参数解析/JSON 输出，不调 PowerShell/网络）----
def test_backend_parse_times_strips_flag():
    import backend_cli
    assert backend_cli._parse_times(["--times", "09:00", "21:00"]) == ["09:00", "21:00"]
    assert backend_cli._parse_times(["08:00"]) == ["08:00"]


def test_backend_parse_times_empty_falls_back_to_prefs(monkeypatch):
    import backend_cli
    import prefs
    monkeypatch.setattr(prefs, "get_crawl_times", lambda: ["09:00", "21:00"])
    assert backend_cli._parse_times([]) == ["09:00", "21:00"]


def test_backend_unknown_command_returns_error(capsys):
    import json
    import backend_cli
    code = backend_cli.main(["nope"])
    assert code == 1
    out = json.loads(capsys.readouterr().out.strip())
    assert out["ok"] is False
    assert "未知子命令" in out["error"]


def test_backend_no_command_returns_error(capsys):
    import json
    import backend_cli
    code = backend_cli.main([])
    assert code == 1
    out = json.loads(capsys.readouterr().out.strip())
    assert out["ok"] is False


def test_backend_prefs_set_invalid_json(capsys):
    import json
    import backend_cli
    code = backend_cli.main(["prefs-set", "{not json"])
    assert code == 1
    out = json.loads(capsys.readouterr().out.strip())
    assert out["ok"] is False
    assert "JSON" in out["error"]


def test_backend_prefs_set_roundtrip(tmp_path, monkeypatch, capsys):
    import json
    import backend_cli
    import prefs
    from nhk_tool import config
    monkeypatch.setattr(config, "DATA_ROOT", tmp_path)
    monkeypatch.setattr(prefs, "_PREFS_PATH", tmp_path / "widget_prefs.json")

    code = backend_cli.main(["prefs-set", json.dumps({"crawl_times": ["08:30", "20:30"]})])
    assert code == 0
    out = json.loads(capsys.readouterr().out.strip())
    assert out["ok"] is True
    assert out["prefs"]["crawl_times"] == ["08:30", "20:30"]


def test_backend_emit_outputs_single_json_line(capsys):
    import json
    import backend_cli
    backend_cli._emit({"hello": "世界", "n": 1})
    captured = capsys.readouterr().out
    assert captured.count("\n") == 1
    assert json.loads(captured.strip()) == {"hello": "世界", "n": 1}


