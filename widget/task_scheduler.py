"""Windows 任务计划程序（Task Scheduler）封装——定时爬取的落地机制。

为什么用任务计划而非进程内定时（APScheduler）：唯有任务计划的 `WakeToRun`
能把电脑从睡眠唤醒来执行；进程内定时在休眠时随进程一起冻结，跨过的时刻直接丢失。

封装方式：Python 调 PowerShell。
- register/unregister 需管理员权限（S4U 任务），用 `Start-Process -Verb RunAs`
  弹 UAC 提权；提权子进程独立异步，退出码不完全可靠，故**注册后一律用只读
  get_status() 以系统真相复核**。
- is_registered/get_status 只读，不需提权。
- 编码避坑（项目踩过 GBK/BOM）：PowerShell 命令体全 ASCII；含动态内容的注册
  脚本用 -EncodedCommand（UTF-16LE+base64）整体传入，绕开命令行引号/编码问题；
  状态查询输出 ASCII 化 JSON，Python 侧 json 解析。

Action 指向 `exe --crawl`（打包态）或 `pythonw app.py --crawl`（源码态），
与 autostart.py::_target_and_args 同一分叉思路。
"""
from __future__ import annotations

import base64
import json
import re
import subprocess
import sys
from pathlib import Path

# 任务名：与旧 scripts/register_task.ps1 一致，避免产生重复任务
TASK_NAME = "NHK-Easy-News-Crawl"
# 任务描述（ASCII，避免 PowerShell 中文编码坑）
_TASK_DESC = "NHK Easy News daily crawl (headless, auto-renew consent cookie)"
_EXEC_TIME_LIMIT_MIN = 15  # 单次执行时限（分钟），超时视为卡死由系统终止

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")  # HH:MM 24 小时
_PS = ["powershell", "-NoProfile", "-NonInteractive"]

# 无控制台窗口运行 PowerShell：打包成无控制台 GUI exe 后，subprocess 调
# powershell 默认会闪一个黑色 cmd 窗口（打开设置触发 get_status 即弹）。
# CREATE_NO_WINDOW 抑制该窗口；非 Windows（理论上不会走到）回退为 0。
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# ---------------------------------------------------------------------------
# Action 目标（打包/源码分叉，参考 autostart.py）
# ---------------------------------------------------------------------------
def _target_and_args() -> tuple[str, str, str]:
    """返回 (execute, argument, workdir)，对应任务计划 Action 三要素。

    额外附带 `--from-task` 标记，供 run_crawl 在日志中区分「定时任务触发」。
    """
    if getattr(sys, "frozen", False):
        # 打包后：直接以 exe --crawl 运行
        exe = sys.executable
        return exe, "--crawl --from-task", str(Path(exe).parent)
    # 源码运行：venv 的 pythonw 跑 widget/app.py --crawl（无控制台窗口）
    app_py = Path(__file__).resolve().parent / "app.py"
    py_dir = Path(sys.executable).parent
    pythonw = py_dir / "pythonw.exe"
    runner = str(pythonw if pythonw.exists() else sys.executable)
    return runner, f'"{app_py}" --crawl --from-task', str(app_py.parent.parent)


def _norm_times(times: list[str]) -> list[str]:
    """过滤合法 HH:MM、去重、排序；为空则回退默认 09:00/21:00。"""
    seen: list[str] = []
    for t in times:
        t = str(t).strip()
        if _TIME_RE.match(t) and t not in seen:
            seen.append(t)
    seen.sort()
    return seen or ["09:00", "21:00"]


def _ps_quote(s: str) -> str:
    """转义为 PowerShell 单引号字符串字面量内容（单引号翻倍）。"""
    return s.replace("'", "''")


# ---------------------------------------------------------------------------
# 注册脚本（在提权子进程中执行）
# ---------------------------------------------------------------------------
def _build_register_script(times: list[str]) -> str:
    """构造注册任务的 PowerShell 脚本（将经 -EncodedCommand 传入提权进程）。"""
    execute, argument, workdir = _target_and_args()
    triggers = "; ".join(
        f"$triggers += New-ScheduledTaskTrigger -Daily -At '{t}'" for t in times
    )
    # -Argument 仅在非空时附加（frozen 态为 '--crawl'，必非空；保险起见仍判空）
    arg_clause = f" -Argument '{_ps_quote(argument)}'" if argument else ""
    return (
        "$ErrorActionPreference='Stop'; "
        f"$action = New-ScheduledTaskAction -Execute '{_ps_quote(execute)}'"
        f"{arg_clause} -WorkingDirectory '{_ps_quote(workdir)}'; "
        "$triggers = @(); "
        f"{triggers}; "
        "$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun "
        "-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries "
        f"-ExecutionTimeLimit (New-TimeSpan -Minutes {_EXEC_TIME_LIMIT_MIN}); "
        "$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME "
        "-LogonType S4U -RunLevel Limited; "
        f"if (Get-ScheduledTask -TaskName '{TASK_NAME}' -ErrorAction SilentlyContinue) "
        f"{{ Unregister-ScheduledTask -TaskName '{TASK_NAME}' -Confirm:$false }}; "
        f"Register-ScheduledTask -TaskName '{TASK_NAME}' -Action $action "
        "-Trigger $triggers -Settings $settings -Principal $principal "
        f"-Description '{_TASK_DESC}' | Out-Null"
    )


def _build_unregister_script() -> str:
    return (
        "$ErrorActionPreference='Stop'; "
        f"if (Get-ScheduledTask -TaskName '{TASK_NAME}' -ErrorAction SilentlyContinue) "
        f"{{ Unregister-ScheduledTask -TaskName '{TASK_NAME}' -Confirm:$false }}"
    )


def _run_elevated(script: str, timeout: int = 120) -> bool:
    """以 UAC 提权运行 PowerShell 脚本（-EncodedCommand 传入），等待其结束。

    返回提权进程是否被成功拉起并正常退出（退出码 0）。注意：这只是"是否跑通"的
    提示，真相以 get_status() 复核为准。UAC 被取消 → Start-Process 抛错 → 返回 False。
    """
    enc = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    # 外层（非提权）拉起提权进程并等待；-PassThru 取退出码；UAC 取消会抛错（catch→exit 1）
    # 提权子进程加 -WindowStyle Hidden，避免其 PowerShell 控制台一闪。
    outer = (
        "try { "
        f"$p = Start-Process powershell -Verb RunAs -WindowStyle Hidden -ArgumentList "
        f"@('-NoProfile','-NonInteractive','-WindowStyle','Hidden','-EncodedCommand','{enc}') "
        "-Wait -PassThru; exit $p.ExitCode "
        "} catch { exit 1 }"
    )
    try:
        proc = subprocess.run(
            _PS + ["-Command", outer],
            capture_output=True, timeout=timeout,
            creationflags=_CREATE_NO_WINDOW,
        )
        return proc.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


# ---------------------------------------------------------------------------
# 对外 API
# ---------------------------------------------------------------------------
def register(times: list[str]) -> bool:
    """注册/更新定时任务（幂等：先注销再注册）。弹 UAC。

    返回值仅供参考，调用方应随后 get_status() 复核系统真相。
    """
    return _run_elevated(_build_register_script(_norm_times(times)))


def unregister() -> bool:
    """注销定时任务。弹 UAC。返回值仅供参考，应随后 get_status() 复核。"""
    return _run_elevated(_build_unregister_script())


def get_status() -> dict:
    """只读查询任务状态（不需提权）。

    返回 dict：
      registered: bool
      state:      str（任务状态，如 Ready/Running/Disabled），未注册时缺省
      next_run:   str（下次运行 'yyyy-MM-dd HH:mm'，无则空串）
      last_run:   str（上次运行时间，同上）
      last_result:int（上次结果码，0 表示成功）
      error:      str（查询失败时的说明，可选）
    """
    script = (
        "$ErrorActionPreference='SilentlyContinue'; "
        f"$t = Get-ScheduledTask -TaskName '{TASK_NAME}' -ErrorAction SilentlyContinue; "
        "if (-not $t) { Write-Output '{\"registered\":false}'; exit 0 }; "
        "$info = $t | Get-ScheduledTaskInfo; "
        "$next = if ($info.NextRunTime) "
        "{ $info.NextRunTime.ToString('yyyy-MM-dd HH:mm') } else { '' }; "
        "$last = if ($info.LastRunTime) "
        "{ $info.LastRunTime.ToString('yyyy-MM-dd HH:mm') } else { '' }; "
        "$o = [ordered]@{ registered=$true; state=[string]$t.State; "
        "next_run=$next; last_run=$last; last_result=$info.LastTaskResult }; "
        "$o | ConvertTo-Json -Compress"
    )
    try:
        proc = subprocess.run(
            _PS + ["-Command", script],
            capture_output=True, timeout=30,
            creationflags=_CREATE_NO_WINDOW,
        )
        out = proc.stdout.decode("utf-8", errors="replace").strip()
        if not out:
            return {"registered": False, "error": "状态查询无输出"}
        return json.loads(out)
    except json.JSONDecodeError:
        return {"registered": False, "error": "状态解析失败"}
    except (subprocess.SubprocessError, OSError) as exc:
        return {"registered": False, "error": f"状态查询失败：{exc}"}


def is_registered() -> bool:
    """任务是否已注册（只读）。"""
    return bool(get_status().get("registered"))
