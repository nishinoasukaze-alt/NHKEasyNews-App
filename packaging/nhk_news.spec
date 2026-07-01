# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec：NHK Easy News 桌面挂件（onedir）。

打包要点：
  - 入口 widget/app.py，GUI 模式（无控制台）；
  - 收集 playwright 包及其 driver；
  - 把本机 ms-playwright 浏览器内核整目录打进 _internal/ms-playwright，
    运行时 app.py 设 PLAYWRIGHT_BROWSERS_PATH 指向它；
  - 带上 src/nhk_tool 源码、widget/*.py、style.qss。

构建：从项目根运行  pyinstaller packaging/nhk_news.spec
输出：dist/NHKEasyNews/NHKEasyNews.exe
"""
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

PROJECT_ROOT = Path(os.getcwd())
WIDGET = PROJECT_ROOT / "widget"
SRC = PROJECT_ROOT / "src"

# Playwright 浏览器内核（本机已 install 的整目录）
MS_PLAYWRIGHT = Path(os.environ["LOCALAPPDATA"]) / "ms-playwright"

datas = []
binaries = []
hiddenimports = []

# playwright 包资源 + driver
pw_datas, pw_bins, pw_hidden = collect_all("playwright")
datas += pw_datas
binaries += pw_bins
hiddenimports += pw_hidden

# apscheduler 动态导入的 trigger/executor + tzlocal
hiddenimports += collect_submodules("apscheduler")
hiddenimports += ["tzlocal", "tzdata"]
# pycryptodome
hiddenimports += collect_submodules("Crypto")

# 把浏览器内核整目录收进 _internal/ms-playwright
for p in MS_PLAYWRIGHT.rglob("*"):
    if p.is_file():
        rel = p.relative_to(MS_PLAYWRIGHT)
        datas.append((str(p), str(Path("ms-playwright") / rel.parent)))

# 业务代码与资源
datas.append((str(WIDGET / "style.qss"), "."))
# 把 src/nhk_tool 整包带上（作为数据，运行时 app.py 已把 src 加入 sys.path 等价；
# 这里直接收集为可导入包更稳）
for p in (SRC / "nhk_tool").rglob("*.py"):
    rel = p.relative_to(SRC)
    datas.append((str(p), str(rel.parent)))
# widget 下的同级模块（task_scheduler 为新定时机制；scheduler 已废弃但保留供测试；
# backend_cli 为 Tauri 壳的 JSON 命令行桥接）
for name in ("ui", "prefs", "scheduler", "task_scheduler", "settings_dialog", "autostart", "resize_util", "backend_cli"):
    f = WIDGET / f"{name}.py"
    if f.exists():
        datas.append((str(f), "."))


block_cipher = None

a = Analysis(
    [str(WIDGET / "app.py")],
    pathex=[str(WIDGET), str(SRC)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "pytest"],
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="NHKEasyNews",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # GUI，无控制台窗口
    disable_windowed_traceback=False,
    icon=str(PROJECT_ROOT / "packaging" / "app.ico"),  # exe 文件图标：粉底白 N
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="NHKEasyNews",
)
