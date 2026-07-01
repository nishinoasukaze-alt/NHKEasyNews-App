"""设置对话框：开机自启 / 退出行为 / 爬取时间段 / Windows 任务计划定时。

任务计划相关的 PowerShell 调用（get_status / register / unregister）较慢，
且 register/unregister 还要等 UAC——一律放到后台 QThread 执行，避免阻塞 UI 线程
导致界面卡顿/无响应。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QThread, QTime, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QRadioButton,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

import prefs
import autostart
import task_scheduler


def _format_status(st: dict) -> str:
    """把 task_scheduler.get_status() 结果转为一行中文状态描述。"""
    if st.get("error"):
        return f"状态：查询失败（{st['error']}）"
    if not st.get("registered"):
        return "状态：未注册"
    parts = ["状态：已注册"]
    if st.get("next_run"):
        parts.append(f"下次 {st['next_run']}")
    lr = st.get("last_result")
    if st.get("last_run"):
        ok = "成功" if lr == 0 else f"结果码 {lr}"
        parts.append(f"上次 {st['last_run']}（{ok}）")
    return "　|　".join(parts)


class _StatusWorker(QThread):
    """后台查询任务状态（只读，不提权）。"""

    done = Signal(dict)

    def run(self) -> None:
        self.done.emit(task_scheduler.get_status())


class _ApplyWorker(QThread):
    """后台执行注册/注销（含 UAC 等待），完成后回传系统真相状态。"""

    done = Signal(bool, dict)  # (register?, 复核后的 status)

    def __init__(self, register: bool, times: list[str], parent=None):
        super().__init__(parent)
        self._register = register
        self._times = times

    def run(self) -> None:
        if self._register:
            task_scheduler.register(self._times)
        else:
            task_scheduler.unregister()
        # 以系统真相为准（提权子进程异步，返回码不完全可靠）
        self.done.emit(self._register, task_scheduler.get_status())


class SettingsDialog(QDialog):
    """设置界面。保存后发 saved 信号。"""

    saved = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("settingsDialog")
        self.setWindowTitle("设置 — NHK Easy News")
        self.setMinimumWidth(360)

        # 记录进入对话框时的初始定时配置，用于判断是否需要提权操作
        self._init_task_enabled = prefs.get_task_enabled()
        self._init_times = prefs.get_crawl_times()
        self._status_worker: _StatusWorker | None = None
        self._apply_worker: _ApplyWorker | None = None

        root = QVBoxLayout(self)
        form = QFormLayout()
        form.setSpacing(10)

        # 开机自启
        self._autostart = QCheckBox("登录 Windows 时自动启动")
        self._autostart.setChecked(prefs.get_autostart())
        form.addRow("开机自启", self._autostart)

        # 退出行为
        exit_box = QWidget()
        exit_lay = QVBoxLayout(exit_box)
        exit_lay.setContentsMargins(0, 0, 0, 0)
        exit_lay.setSpacing(2)
        self._exit_tray = QRadioButton("退到后台运行（托盘/任务栏可恢复）")
        self._exit_quit = QRadioButton("退出程序")
        ca = prefs.get_close_action()
        if ca == "quit":
            self._exit_quit.setChecked(True)
        else:
            self._exit_tray.setChecked(True)  # 默认/tray
        exit_lay.addWidget(self._exit_tray)
        exit_lay.addWidget(self._exit_quit)
        form.addRow("关闭按钮", exit_box)

        # 爬取时间段（早 / 晚两个）
        times = self._init_times
        t1 = times[0] if len(times) >= 1 else "09:00"
        t2 = times[1] if len(times) >= 2 else "21:00"
        time_box = QWidget()
        time_lay = QHBoxLayout(time_box)
        time_lay.setContentsMargins(0, 0, 0, 0)
        self._time1 = QTimeEdit(QTime.fromString(t1, "HH:mm"))
        self._time2 = QTimeEdit(QTime.fromString(t2, "HH:mm"))
        for te in (self._time1, self._time2):
            te.setDisplayFormat("HH:mm")
            te.setWrapping(True)  # 到边界循环：上/下按钮始终可用（解决"只有下能用"）
            te.setButtonSymbols(QTimeEdit.UpDownArrows)
            te.setAlignment(Qt.AlignCenter)
        time_lay.addWidget(self._time1)
        time_lay.addWidget(QLabel("／"))
        time_lay.addWidget(self._time2)
        time_lay.addStretch(1)
        form.addRow("每日爬取时间", time_box)

        # Windows 任务计划定时
        self._task_enabled = QCheckBox("启用 Windows 定时任务（睡眠也能唤醒执行）")
        self._task_enabled.setChecked(self._init_task_enabled)
        form.addRow("定时任务", self._task_enabled)

        # 任务状态（只读，反映系统真相；后台异步查询）
        self._task_status = QLabel("状态：查询中…")
        self._task_status.setObjectName("settingsHint")
        self._task_status.setWordWrap(True)
        form.addRow("", self._task_status)

        root.addLayout(form)

        hint = QLabel(
            "提示：定时由 Windows 任务计划独立运行，挂件无需常驻。\n"
            "启用/禁用/改时间需管理员授权（会弹 UAC）。\n"
            "唤醒执行依赖系统「允许唤醒计时器」开启；完全关机/休眠不保证唤醒。\n"
            "同意 cookie 过期会在爬取时自动续期，一般无需手动操作。"
        )
        hint.setObjectName("settingsHint")
        hint.setWordWrap(True)
        root.addWidget(hint)

        self._btns = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel
        )
        self._btns.accepted.connect(self._on_save)
        self._btns.rejected.connect(self.reject)
        root.addWidget(self._btns)

        # 打开即异步查询任务状态，避免同步调 PowerShell 卡住界面打开
        self._start_status_query()

    # ---------------------- 状态查询（异步）----------------------
    def _start_status_query(self) -> None:
        self._task_status.setText("状态：查询中…")
        self._status_worker = _StatusWorker(self)
        self._status_worker.done.connect(self._on_status_ready)
        self._status_worker.start()

    def _on_status_ready(self, st: dict) -> None:
        self._task_status.setText(_format_status(st))

    # ---------------------- 保存 ----------------------
    def _on_save(self) -> None:
        # 非任务计划项很快，同步保存即可
        want_auto = self._autostart.isChecked()
        autostart.apply(want_auto)
        prefs.set_autostart(want_auto and autostart.is_enabled())
        prefs.set_close_action("quit" if self._exit_quit.isChecked() else "tray")

        t1 = self._time1.time().toString("HH:mm")
        t2 = self._time2.time().toString("HH:mm")
        saved_times = prefs.set_crawl_times([t1, t2])

        # 定时任务：仅在启用状态或时间真正变化时才提权操作（避免每次保存都弹 UAC）
        want_task = self._task_enabled.isChecked()
        times_changed = saved_times != self._init_times
        state_changed = want_task != self._init_task_enabled
        if want_task and (state_changed or times_changed):
            self._start_apply(register=True, times=saved_times)
        elif not want_task and state_changed:
            self._start_apply(register=False, times=saved_times)
        else:
            # 无需动任务计划：直接发信号并关闭
            self.saved.emit()
            self.accept()

    def _start_apply(self, register: bool, times: list[str]) -> None:
        """后台执行注册/注销（含 UAC），期间禁用按钮并提示，完成后关闭对话框。"""
        self._btns.setEnabled(False)
        self._task_status.setText(
            "正在配置定时任务…请在弹出的 UAC 窗口点「是」授权（勿关闭本窗口）"
        )
        self._apply_worker = _ApplyWorker(register, times, self)
        self._apply_worker.done.connect(self._on_apply_done)
        self._apply_worker.start()

    def _on_apply_done(self, register: bool, st: dict) -> None:
        actually_registered = bool(st.get("registered"))
        prefs.set_task_enabled(actually_registered)
        self._task_status.setText(_format_status(st))
        self._btns.setEnabled(True)

        if register and not actually_registered:
            QMessageBox.warning(
                self, "定时任务未启用",
                "未能注册定时任务。可能是 UAC 授权被取消或权限不足，请重试。",
            )
            return  # 不关闭，让用户重试
        if not register and actually_registered:
            QMessageBox.warning(
                self, "定时任务未注销",
                "未能注销定时任务。可能是 UAC 授权被取消，请重试。",
            )
            return

        self.saved.emit()
        self.accept()

    # ---------------------- 收尾 ----------------------
    def reject(self) -> None:
        # 等待后台线程结束，避免对话框析构时线程仍在跑
        for w in (self._status_worker, self._apply_worker):
            if w is not None and w.isRunning():
                w.wait(3000)
        super().reject()
