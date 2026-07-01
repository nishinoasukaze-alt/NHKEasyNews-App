"""PySide6 桌面挂件主程序。

读取最新一日的 manifest.json，展示四条新闻。无边框、置顶、可拖动、可缩放。
可最小化到系统托盘，方便随时启用。运行：python widget/app.py
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

# 导入路径：源码运行用项目内 src/widget；打包(frozen)用 _internal（含 nhk_tool 与同级模块）
if getattr(sys, "frozen", False):
    _INTERNAL = Path(sys.executable).parent / "_internal"
    for _p in (str(_INTERNAL), str(Path(sys.executable).parent)):
        if _p not in sys.path:
            sys.path.insert(0, _p)
else:
    _SRC = Path(__file__).resolve().parents[1] / "src"
    if str(_SRC) not in sys.path:
        sys.path.insert(0, str(_SRC))
    _HERE = Path(__file__).resolve().parent
    if str(_HERE) not in sys.path:
        sys.path.insert(0, str(_HERE))


def _setup_playwright_browsers_path() -> None:
    """打包(frozen)时让 Playwright 找到随包分发的 Chromium 内核。

    PyInstaller 把内核收集到 _internal/ms-playwright，需在 import playwright 前
    设 PLAYWRIGHT_BROWSERS_PATH。源码运行不设，用系统默认位置。
    """
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).parent
        for cand in (base / "_internal" / "ms-playwright", base / "ms-playwright"):
            if cand.exists():
                os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(cand))
                break


_setup_playwright_browsers_path()

from PySide6.QtCore import Qt, QPoint, QTimer
from PySide6.QtGui import QAction, QPixmap, QPainter, QFont, QColor, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QCheckBox,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QSystemTrayIcon,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from nhk_tool import config, storage  # noqa: E402
from ui import NewsCard, DetailView  # noqa: E402
from resize_util import hit_test  # noqa: E402
import prefs  # noqa: E402
from settings_dialog import SettingsDialog  # noqa: E402

# 方向字符串 → Qt 光标
_CURSORS = {
    "left": Qt.SizeHorCursor,
    "right": Qt.SizeHorCursor,
    "top": Qt.SizeVerCursor,
    "bottom": Qt.SizeVerCursor,
    "top-left": Qt.SizeFDiagCursor,
    "bottom-right": Qt.SizeFDiagCursor,
    "top-right": Qt.SizeBDiagCursor,
    "bottom-left": Qt.SizeBDiagCursor,
}

# 方向字符串 → Qt.Edge 组合（startSystemResize 用）
_EDGES = {
    "left": Qt.LeftEdge,
    "right": Qt.RightEdge,
    "top": Qt.TopEdge,
    "bottom": Qt.BottomEdge,
    "top-left": Qt.TopEdge | Qt.LeftEdge,
    "top-right": Qt.TopEdge | Qt.RightEdge,
    "bottom-left": Qt.BottomEdge | Qt.LeftEdge,
    "bottom-right": Qt.BottomEdge | Qt.RightEdge,
}


class NewsWidget(QWidget):
    """无边框、置顶、可拖动、可缩放的常驻挂件窗口。"""

    def __init__(self):
        super().__init__()
        self._drag_pos: QPoint | None = None
        self._items: list = []
        self._day_dir: Path | None = None

        self.setWindowTitle(config.WIDGET_TITLE)
        # 无边框 + 置顶；不用 Qt.Tool，以便最小化时任务栏可见、可恢复
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
        )
        # 可缩放：设最小尺寸而非固定宽度
        self.setMinimumSize(config.WIDGET_MIN_WIDTH, config.WIDGET_MIN_HEIGHT)
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self.setMouseTracking(True)  # 未按下时也接收 move，用于更新缩放光标

        self._build_ui()
        self._load_qss()

    # ---------------------- UI ----------------------
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # 标题栏（含关闭/刷新）
        bar = QHBoxLayout()
        bar.setContentsMargins(10, 6, 10, 6)
        title = QLabel(config.WIDGET_TITLE)
        title.setObjectName("barTitle")
        bar.addWidget(title)

        # 刷新状态反馈标签（刷新后短暂显示“已更新 时刻”）
        self._status_label = QLabel("")
        self._status_label.setObjectName("statusLabel")
        bar.addWidget(self._status_label)

        bar.addStretch(1)

        settings_btn = QPushButton("⚙")
        settings_btn.setObjectName("iconBtn")
        settings_btn.setToolTip("设置")
        settings_btn.setCursor(Qt.PointingHandCursor)
        settings_btn.clicked.connect(self.open_settings)
        bar.addWidget(settings_btn)

        refresh = QPushButton("⟳")
        refresh.setObjectName("iconBtn")
        refresh.setToolTip("更新 / 刷新")
        refresh.setCursor(Qt.PointingHandCursor)
        refresh.clicked.connect(self.manual_refresh)
        bar.addWidget(refresh)

        # 最小化按钮：退到后台运行，任务栏图标可恢复
        minimize = QPushButton("—")
        minimize.setObjectName("iconBtn")
        minimize.setToolTip("最小化（任务栏可恢复）")
        minimize.setCursor(Qt.PointingHandCursor)
        minimize.clicked.connect(self.showMinimized)
        bar.addWidget(minimize)

        # 关闭按钮：弹「退到后台 / 退出程序」对话框（可记住选择）
        close = QPushButton("✕")
        close.setObjectName("iconBtn")
        close.setToolTip("关闭")
        close.setCursor(Qt.PointingHandCursor)
        close.clicked.connect(self._on_close_clicked)
        bar.addWidget(close)

        bar_w = QWidget()
        bar_w.setObjectName("titleBar")
        bar_w.setLayout(bar)
        bar_w.setMouseTracking(True)
        outer.addWidget(bar_w)

        # 堆叠视图：列表页(0) / 详情页(1)
        self._stack = QStackedWidget()
        self._stack.setMouseTracking(True)

        # --- 列表页 ---
        self._content = QVBoxLayout()
        self._content.setContentsMargins(8, 8, 8, 8)
        self._content.setSpacing(8)
        content_w = QWidget()
        content_w.setObjectName("content")
        content_w.setLayout(self._content)
        content_w.setMouseTracking(True)

        scroll = QScrollArea()
        scroll.setObjectName("scroll")
        scroll.setWidgetResizable(True)
        scroll.setWidget(content_w)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setMouseTracking(True)
        self._stack.addWidget(scroll)          # index 0

        # --- 详情页 ---
        self._detail = DetailView()
        self._detail.back.connect(self._show_list)
        self._stack.addWidget(self._detail)    # index 1

        outer.addWidget(self._stack)

        self.reload()

    def _load_qss(self) -> None:
        qss_path = Path(__file__).with_name("style.qss")
        if qss_path.exists():
            self.setStyleSheet(qss_path.read_text(encoding="utf-8"))

    # ---------------------- 设置 ----------------------
    def open_settings(self) -> None:
        dlg = SettingsDialog(self)
        dlg.saved.connect(self._on_settings_saved)
        dlg.exec()

    def _on_settings_saved(self) -> None:
        # 定时已交给 Windows 任务计划（在设置对话框内完成注册），此处仅给出反馈。
        self._flash_status("✓ 设置已保存")

    # ---------------------- 数据 ----------------------
    def manual_refresh(self) -> None:
        """手动刷新：重载数据并在标题栏给出可见反馈。"""
        before = self._current_news_ids()
        self.reload()
        after = self._current_news_ids()
        now = datetime.now().strftime("%H:%M:%S")
        if after and after != before:
            self._flash_status(f"✓ 已更新 {now}")
        elif after:
            self._flash_status(f"✓ 最新 {now}")
        else:
            self._flash_status(f"⚠ 暂无数据 {now}")

    def _current_news_ids(self) -> list[str]:
        found = storage.latest_manifest()
        if not found:
            return []
        return [it.get("news_id") for it in found[1].get("items", [])]

    def _flash_status(self, text: str) -> None:
        """标题栏短暂显示状态文字，数秒后自动清除。"""
        self._status_label.setText(text)
        QTimer.singleShot(4000, lambda: self._status_label.setText(""))

    def reload(self) -> None:
        """重新加载最新 manifest 并渲染卡片。"""
        # 回到列表页
        self._stack.setCurrentIndex(0)
        # 清空旧卡片
        while self._content.count():
            child = self._content.takeAt(0)
            w = child.widget()
            if w is not None:
                w.deleteLater()

        self._items = []
        self._day_dir = None

        found = storage.latest_manifest()
        if found is None:
            self._show_placeholder("まだニュースがありません。\n先に取得スクリプトを実行してください。")
            return

        manifest_path, data = found
        day_dir = manifest_path.parent
        items = data.get("items", [])
        if not items:
            self._show_placeholder("ニュースが空です。")
            return

        # 缓存供详情页使用
        self._items = items
        self._day_dir = day_dir

        # 日期标签
        date_label = QLabel(f"📅 {data.get('date', '')}")
        date_label.setObjectName("dateLabel")
        self._content.addWidget(date_label)

        # 无更新提示条（仍在下方展示最新四条）
        if not data.get("has_update", True):
            notice = QLabel(config.WIDGET_NO_UPDATE_NOTICE)
            notice.setObjectName("updateNotice")
            notice.setWordWrap(True)
            self._content.addWidget(notice)

        for idx, it in enumerate(items):
            body_preview = self._read_preview(day_dir, it.get("body"))
            image_file = self._resolve(day_dir, it.get("image"))
            card = NewsCard(
                index=idx,
                title=it.get("title", "(無題)"),
                body_preview=body_preview,
                image_file=image_file,
                is_new=it.get("is_new", False),
            )
            card.clicked.connect(self._open_detail)
            self._content.addWidget(card)

        self._content.addStretch(1)

    # ---------------------- 详情页切换 ----------------------
    def _open_detail(self, index: int) -> None:
        """打开第 index 条新闻的详情页。"""
        if not self._items or index >= len(self._items) or self._day_dir is None:
            return
        it = self._items[index]
        body_text = self._read_full_body(self._day_dir, it.get("body"))
        image_file = self._resolve(self._day_dir, it.get("image"))
        audio_file = self._resolve(self._day_dir, it.get("audio"))
        self._detail.show_news(
            title=it.get("title", "(無題)"),
            body_text=body_text,
            publish_time=it.get("publish_time", ""),
            image_file=image_file,
            audio_file=audio_file,
        )
        self._stack.setCurrentIndex(1)

    def _show_list(self) -> None:
        self._stack.setCurrentIndex(0)

    def _read_full_body(self, day_dir: Path, body_rel: str | None) -> str:
        p = self._resolve(day_dir, body_rel)
        if p is None:
            return ""
        try:
            return p.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    @staticmethod
    def _resolve(day_dir: Path, rel: str | None) -> Path | None:
        if not rel:
            return None
        p = day_dir / rel
        return p if p.exists() else None

    def _read_preview(self, day_dir: Path, body_rel: str | None) -> str:
        p = self._resolve(day_dir, body_rel)
        if p is None:
            return ""
        try:
            text = p.read_text(encoding="utf-8").replace("\n", " ").strip()
        except OSError:
            return ""
        limit = config.WIDGET_BODY_PREVIEW_CHARS
        return text[:limit] + ("…" if len(text) > limit else "")

    def _show_placeholder(self, msg: str) -> None:
        label = QLabel(msg)
        label.setObjectName("placeholder")
        label.setAlignment(Qt.AlignCenter)
        label.setWordWrap(True)
        self._content.addWidget(label)
        self._content.addStretch(1)

    # ---------------------- 关闭/托盘 ----------------------
    def _on_close_clicked(self) -> None:
        """点✕：按已记住偏好执行；未记住则弹对话框询问。"""
        action = prefs.get_close_action()
        if action == "quit":
            QApplication.quit()
            return
        if action == "tray":
            self._go_background()
            return
        self._ask_close_action()

    def _go_background(self) -> None:
        """退到后台运行：有托盘则收进托盘，否则最小化。"""
        if config.WIDGET_ENABLE_TRAY and QSystemTrayIcon.isSystemTrayAvailable():
            self.hide()
        else:
            self.showMinimized()

    def _ask_close_action(self) -> None:
        """弹出「退到后台 / 退出程序」对话框，可勾选记住选择。"""
        box = QMessageBox(self)
        box.setWindowTitle("关闭 NHK Easy News")
        box.setText("要如何关闭？")
        box.setInformativeText(
            "「退到后台」：程序继续在后台运行，可从托盘/任务栏恢复。\n"
            "「退出程序」：完全退出。"
        )
        bg_btn = box.addButton("退到后台运行", QMessageBox.AcceptRole)
        quit_btn = box.addButton("退出程序", QMessageBox.DestructiveRole)
        cancel_btn = box.addButton("取消", QMessageBox.RejectRole)
        box.setDefaultButton(bg_btn)

        remember = QCheckBox("记住我的选择，下次不再提示")
        box.setCheckBox(remember)

        box.exec()
        clicked = box.clickedButton()
        if clicked is cancel_btn:
            return
        if clicked is quit_btn:
            if remember.isChecked():
                prefs.set_close_action("quit")
            QApplication.quit()
        else:  # 退到后台
            if remember.isChecked():
                prefs.set_close_action("tray")
            self._go_background()

    # ---------------------- 拖动 + 缩放 ----------------------
    def _edge_at(self, pos: QPoint) -> str | None:
        return hit_test(
            pos.x(), pos.y(), self.width(), self.height(), config.RESIZE_MARGIN
        )

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            edge = self._edge_at(event.position().toPoint())
            if edge is not None:
                # 交给窗管做缩放（高 DPI / 多屏更稳）
                handle = self.windowHandle()
                if handle is not None:
                    handle.startSystemResize(_EDGES[edge])
                    event.accept()
                    return
            # 非边缘：标题栏拖动移动
            self._drag_pos = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )
            event.accept()

    def mouseMoveEvent(self, event) -> None:
        # 未按键时按命中边缘更新光标
        if not (event.buttons() & Qt.LeftButton):
            edge = self._edge_at(event.position().toPoint())
            self.setCursor(_CURSORS.get(edge, Qt.ArrowCursor))
            return
        # 按住左键且处于移动模式
        if self._drag_pos is not None:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:
        self._drag_pos = None
        self.setCursor(Qt.ArrowCursor)


def _make_app_icon() -> QIcon:
    """生成带 “N” 字母的应用/托盘图标（NHK 风格粉底白字），不依赖外部文件。"""
    size = 64
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing)
    # 圆角粉色底
    painter.setBrush(QColor("#ff5c7a"))
    painter.setPen(Qt.NoPen)
    painter.drawRoundedRect(2, 2, size - 4, size - 4, 14, 14)
    # 白色 “N”
    painter.setPen(QColor("#ffffff"))
    font = QFont("Arial Black", 34, QFont.Bold)
    painter.setFont(font)
    painter.drawText(pix.rect(), Qt.AlignCenter, "N")
    painter.end()
    return QIcon(pix)


def _make_tray(app: QApplication, widget: NewsWidget) -> QSystemTrayIcon | None:
    """创建系统托盘图标与菜单；不可用时返回 None。"""
    if not config.WIDGET_ENABLE_TRAY or not QSystemTrayIcon.isSystemTrayAvailable():
        return None

    icon = _make_app_icon()
    tray = QSystemTrayIcon(icon, parent=widget)
    tray.setToolTip(config.WIDGET_TITLE)

    menu = QMenu()
    act_show = QAction("表示 / 隐藏", widget)
    act_refresh = QAction("更新 / 刷新", widget)
    act_settings = QAction("设置", widget)
    act_reset = QAction("重置关闭偏好", widget)
    act_quit = QAction("終了 / 退出", widget)

    def toggle():
        if widget.isVisible():
            widget.hide()
        else:
            widget.showNormal()
            widget.raise_()
            widget.activateWindow()

    act_show.triggered.connect(toggle)
    act_refresh.triggered.connect(widget.manual_refresh)
    act_settings.triggered.connect(widget.open_settings)
    act_reset.triggered.connect(lambda: prefs.set_close_action(None))
    act_quit.triggered.connect(QApplication.quit)
    menu.addAction(act_show)
    menu.addAction(act_refresh)
    menu.addAction(act_settings)
    menu.addSeparator()
    menu.addAction(act_reset)
    menu.addAction(act_quit)
    tray.setContextMenu(menu)

    # 左键单击托盘图标切换显隐
    def on_activated(reason):
        if reason == QSystemTrayIcon.Trigger:
            toggle()

    tray.activated.connect(on_activated)
    tray.show()
    return tray


def main() -> int:
    # 纯爬取模式：任务计划以 `exe --crawl` 调用——不开窗、跑完即退出。
    # sys.path / PLAYWRIGHT_BROWSERS_PATH 已在模块 import 期设好，此处直接复用；
    # 日志由 run_crawl 内的 FileHandler 落到 exe 旁 data/logs，不依赖控制台。
    if "--crawl" in sys.argv[1:]:
        # --from-task：由 Windows 任务计划触发（见 task_scheduler._target_and_args），
        # 标记触发来源供 run_crawl 写入日志。
        if "--from-task" in sys.argv[1:]:
            os.environ.setdefault("NHK_CRAWL_SOURCE", "scheduled")
        from nhk_tool import run_crawl
        return run_crawl.main()

    # 后端命令行桥接：Tauri(Rust) 壳以 `exe backend <cmd>` 调用本进程做
    # 数据查询/爬取/任务计划/偏好操作，stdout 输出单行 JSON 供 Rust 解析。
    # 设 NHK_LOG_STDERR：让 logger 控制台输出改走 stderr，不污染 stdout 的 JSON。
    if len(sys.argv) >= 2 and sys.argv[1] == "backend":
        os.environ["NHK_LOG_STDERR"] = "1"
        import backend_cli
        return backend_cli.main(sys.argv[2:])

    app = QApplication(sys.argv)
    app.setWindowIcon(_make_app_icon())  # 任务栏 / Alt+Tab 显示 “N” 图标
    w = NewsWidget()
    w.resize(config.WIDGET_WIDTH, config.WIDGET_INIT_HEIGHT)

    tray = _make_tray(app, w)
    if tray is not None:
        # 有托盘：关闭最后窗口不退出，靠托盘菜单退出
        app.setQuitOnLastWindowClosed(False)

    # 初始定位到主屏右上角（桌面挂件常见位置），避免出现在屏幕外/被遮挡
    screen = app.primaryScreen()
    if screen is not None:
        area = screen.availableGeometry()
        margin = 20
        x = area.right() - w.width() - margin
        y = area.top() + margin
        w.move(max(area.left(), x), y)

    w.show()
    w.raise_()
    w.activateWindow()

    # 定时由 Windows 任务计划独立运行（见 task_scheduler.py），挂件不再内置调度。
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
