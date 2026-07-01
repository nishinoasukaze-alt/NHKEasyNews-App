"""生成 exe 图标 packaging/app.ico（粉底白 N，多尺寸）。

不依赖 Pillow：用 Qt 渲染各尺寸 PNG，再手工拼成 ICO 容器（ICO=目录+多张PNG）。
运行：python packaging/make_icon.py
"""
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "widget"))

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QPixmap, QPainter, QFont, QColor
from PySide6.QtCore import Qt, QBuffer, QIODevice

SIZES = [16, 24, 32, 48, 64, 128, 256]
OUT = Path(__file__).resolve().parent / "app.ico"


def render_png(size: int) -> bytes:
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    m = max(1, size // 32)
    r = max(2, size // 8)
    p.setBrush(QColor("#ff5c7a"))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(m, m, size - 2 * m, size - 2 * m, r, r)
    p.setPen(QColor("#ffffff"))
    p.setFont(QFont("Arial Black", int(size * 0.55), QFont.Bold))
    p.drawText(pix.rect(), Qt.AlignCenter, "N")
    p.end()
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    pix.save(buf, "PNG")
    return bytes(buf.data())


def build_ico(pngs: list[tuple[int, bytes]]) -> bytes:
    # ICONDIR: reserved(0), type(1=icon), count
    header = struct.pack("<HHH", 0, 1, len(pngs))
    entries = b""
    data = b""
    offset = 6 + 16 * len(pngs)
    for size, png in pngs:
        w = 0 if size >= 256 else size
        h = 0 if size >= 256 else size
        # ICONDIRENTRY: w,h,colorcount,reserved,planes,bitcount,bytesize,offset
        entries += struct.pack("<BBBBHHII", w, h, 0, 0, 1, 32, len(png), offset)
        data += png
        offset += len(png)
    return header + entries + data


def main():
    app = QApplication(sys.argv)
    pngs = [(s, render_png(s)) for s in SIZES]
    OUT.write_bytes(build_ico(pngs))
    print(f"已生成图标：{OUT}（{len(SIZES)} 个尺寸，{OUT.stat().st_size} 字节）")


if __name__ == "__main__":
    main()
