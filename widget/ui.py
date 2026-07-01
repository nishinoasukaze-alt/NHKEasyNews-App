"""挂件 UI 组件：新闻卡片（列表项）与详情视图。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)


def _fmt_ms(ms: int) -> str:
    """毫秒 → m:ss。"""
    s = max(0, ms // 1000)
    return f"{s // 60}:{s % 60:02d}"


class AudioPlayer(QWidget):
    """本地音频播放器：播放/暂停按钮 + 进度条 + 时间标签。

    进度条随播放实时更新，可拖动跳转。延迟创建 QMediaPlayer（首次播放时）。
    """

    def __init__(self, audio_file: Path, parent=None):
        super().__init__(parent)
        self.setObjectName("audioPlayer")
        self._audio_file = audio_file
        self._player: QMediaPlayer | None = None
        self._audio_out: QAudioOutput | None = None
        self._seeking = False  # 用户拖动进度条期间，暂停跟随更新

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        self._btn = QPushButton("▶")
        self._btn.setObjectName("playBtn")
        self._btn.setCursor(Qt.PointingHandCursor)
        self._btn.setFixedWidth(40)
        self._btn.clicked.connect(self._toggle)
        lay.addWidget(self._btn)

        self._slider = QSlider(Qt.Horizontal)
        self._slider.setObjectName("audioSlider")
        self._slider.setRange(0, 0)
        self._slider.sliderPressed.connect(self._on_seek_start)
        self._slider.sliderReleased.connect(self._on_seek_end)
        lay.addWidget(self._slider, stretch=1)

        self._time = QLabel("0:00 / 0:00")
        self._time.setObjectName("audioTime")
        lay.addWidget(self._time)

    def _ensure_player(self) -> None:
        if self._player is not None:
            return
        self._player = QMediaPlayer(self)
        self._audio_out = QAudioOutput(self)
        self._player.setAudioOutput(self._audio_out)
        self._player.setSource(QUrl.fromLocalFile(str(self._audio_file)))
        self._player.playbackStateChanged.connect(self._on_state)
        self._player.positionChanged.connect(self._on_position)
        self._player.durationChanged.connect(self._on_duration)

    def _toggle(self) -> None:
        self._ensure_player()
        if self._player.playbackState() == QMediaPlayer.PlayingState:
            self._player.pause()
        else:
            self._player.play()

    def _on_state(self, state) -> None:
        self._btn.setText("⏸" if state == QMediaPlayer.PlayingState else "▶")

    def _on_duration(self, dur: int) -> None:
        self._slider.setRange(0, dur)
        self._update_time(self._player.position() if self._player else 0, dur)

    def _on_position(self, pos: int) -> None:
        if not self._seeking:
            self._slider.setValue(pos)
        dur = self._player.duration() if self._player else 0
        self._update_time(pos, dur)

    def _on_seek_start(self) -> None:
        self._seeking = True

    def _on_seek_end(self) -> None:
        self._seeking = False
        if self._player is not None:
            self._player.setPosition(self._slider.value())

    def _update_time(self, pos: int, dur: int) -> None:
        self._time.setText(f"{_fmt_ms(pos)} / {_fmt_ms(dur)}")

    def stop(self) -> None:
        if self._player is not None:
            self._player.stop()


class NewsCard(QFrame):
    """列表项卡片：缩略图 + 标题 + 正文预览，整卡可点击进入详情。"""

    clicked = Signal(int)  # 发出该卡片的索引

    def __init__(
        self,
        index: int,
        title: str,
        body_preview: str,
        image_file: Path | None,
        is_new: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self.setObjectName("newsCard")
        self._index = index
        self.setCursor(Qt.PointingHandCursor)

        root = QHBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(10)

        # 缩略图
        if image_file and image_file.exists():
            thumb = QLabel()
            thumb.setObjectName("thumb")
            pix = QPixmap(str(image_file))
            if not pix.isNull():
                thumb.setPixmap(
                    pix.scaled(
                        96, 96,
                        Qt.KeepAspectRatioByExpanding,
                        Qt.SmoothTransformation,
                    )
                )
                thumb.setFixedSize(96, 96)
                thumb.setAlignment(Qt.AlignCenter)
                root.addWidget(thumb)

        # 右侧文字区
        right = QVBoxLayout()
        right.setSpacing(4)

        title_row = QHBoxLayout()
        title_row.setSpacing(6)
        if is_new:
            badge = QLabel("NEW")
            badge.setObjectName("newBadge")
            title_row.addWidget(badge, alignment=Qt.AlignTop)
        title_label = QLabel(title)
        title_label.setObjectName("title")
        title_label.setWordWrap(True)
        title_row.addWidget(title_label, stretch=1)
        right.addLayout(title_row)

        body_label = QLabel(body_preview)
        body_label.setObjectName("body")
        body_label.setWordWrap(True)
        right.addWidget(body_label)

        # 提示“点击查看全文”
        hint = QLabel("クリックで全文 ▸")
        hint.setObjectName("cardHint")
        right.addWidget(hint, alignment=Qt.AlignRight)

        right.addStretch(1)
        root.addLayout(right, stretch=1)

    def mouseReleaseEvent(self, event) -> None:
        # 整卡点击进入详情（左键且在卡片内释放）
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit(self._index)
        super().mouseReleaseEvent(event)


class DetailView(QWidget):
    """新闻详情视图：返回按钮 + 大图 + 完整正文 + 音频。"""

    back = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("detailView")
        self._audio_btn: AudioPlayer | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # 顶部返回栏
        top = QHBoxLayout()
        top.setContentsMargins(8, 6, 8, 6)
        back_btn = QPushButton("← 戻る")
        back_btn.setObjectName("backBtn")
        back_btn.setCursor(Qt.PointingHandCursor)
        back_btn.clicked.connect(self._on_back)
        top.addWidget(back_btn)
        top.addStretch(1)
        outer.addLayout(top)

        # 可滚动内容
        self._body_layout = QVBoxLayout()
        self._body_layout.setContentsMargins(12, 8, 12, 12)
        self._body_layout.setSpacing(10)
        inner = QWidget()
        inner.setObjectName("detailContent")
        inner.setLayout(self._body_layout)

        scroll = QScrollArea()
        scroll.setObjectName("detailScroll")
        scroll.setWidgetResizable(True)
        scroll.setWidget(inner)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(scroll)

    def _on_back(self) -> None:
        if self._audio_btn is not None:
            self._audio_btn.stop()
        self.back.emit()

    def _clear(self) -> None:
        if self._audio_btn is not None:
            self._audio_btn.stop()
            self._audio_btn = None
        while self._body_layout.count():
            child = self._body_layout.takeAt(0)
            w = child.widget()
            if w is not None:
                w.deleteLater()

    def show_news(
        self,
        title: str,
        body_text: str,
        publish_time: str,
        image_file: Path | None,
        audio_file: Path | None,
    ) -> None:
        """用一条新闻的完整内容填充详情视图。"""
        self._clear()

        title_label = QLabel(title)
        title_label.setObjectName("detailTitle")
        title_label.setWordWrap(True)
        self._body_layout.addWidget(title_label)

        if publish_time:
            time_label = QLabel(publish_time)
            time_label.setObjectName("detailTime")
            self._body_layout.addWidget(time_label)

        # 大图（按宽度自适应缩放，保留原比例）
        if image_file and image_file.exists():
            pix = QPixmap(str(image_file))
            if not pix.isNull():
                img_label = QLabel()
                img_label.setObjectName("detailImage")
                img_label.setAlignment(Qt.AlignCenter)
                img_label.setPixmap(
                    pix.scaledToWidth(320, Qt.SmoothTransformation)
                )
                self._body_layout.addWidget(img_label)

        # 音频
        if audio_file and audio_file.exists():
            self._audio_btn = AudioPlayer(audio_file)
            self._body_layout.addWidget(self._audio_btn)

        # 完整正文
        body_label = QLabel(body_text or "(本文なし)")
        body_label.setObjectName("detailBody")
        body_label.setWordWrap(True)
        body_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._body_layout.addWidget(body_label)

        self._body_layout.addStretch(1)
