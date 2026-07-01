"""开机自启：在 Windows 启动文件夹创建/删除指向本程序的快捷方式（.lnk）。

不碰注册表/任务计划——仅在用户启动文件夹放一个 .lnk，登录即运行。
打包(exe)时指向 sys.executable；源码运行时指向 pythonw + app.py（便于开发期测试）。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_LNK_NAME = "NHK-Easy-News.lnk"


def _startup_dir() -> Path:
    appdata = os.environ.get("APPDATA", "")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def _lnk_path() -> Path:
    return _startup_dir() / _LNK_NAME


def _target_and_args() -> tuple[str, str, str]:
    """返回 (target, arguments, workdir)。"""
    if getattr(sys, "frozen", False):
        # 打包后：直接指向 exe
        exe = sys.executable
        return exe, "", str(Path(exe).parent)
    # 源码运行：用 venv 的 pythonw 启动 widget/app.py（无控制台窗口）
    app_py = Path(__file__).resolve().parent / "app.py"
    py_dir = Path(sys.executable).parent
    pythonw = py_dir / "pythonw.exe"
    runner = str(pythonw if pythonw.exists() else sys.executable)
    return runner, f'"{app_py}"', str(app_py.parent.parent)


def is_enabled() -> bool:
    return _lnk_path().exists()


def enable() -> bool:
    """在启动文件夹创建快捷方式。成功返回 True。"""
    target, args, workdir = _target_and_args()
    lnk = _lnk_path()
    lnk.parent.mkdir(parents=True, exist_ok=True)
    ps = (
        "$ws = New-Object -ComObject WScript.Shell; "
        f"$s = $ws.CreateShortcut('{lnk}'); "
        f"$s.TargetPath = '{target}'; "
        f"$s.Arguments = '{args}'; "
        f"$s.WorkingDirectory = '{workdir}'; "
        "$s.WindowStyle = 7; "
        "$s.Description = 'NHK Easy News 桌面挂件'; "
        "$s.Save()"
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            check=True, capture_output=True, timeout=20,
        )
        return lnk.exists()
    except (subprocess.SubprocessError, OSError):
        return False


def disable() -> bool:
    """删除启动文件夹中的快捷方式。成功（或本就不存在）返回 True。"""
    lnk = _lnk_path()
    try:
        if lnk.exists():
            lnk.unlink()
        return not lnk.exists()
    except OSError:
        return False


def apply(enabled: bool) -> bool:
    """按期望状态落地（幂等）。"""
    return enable() if enabled else disable()
