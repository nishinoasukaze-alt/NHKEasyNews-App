"""无边框窗口边缘缩放的命中检测（纯函数，便于离线单测，不依赖 Qt）。

返回方向字符串，由调用方映射到光标与 Qt.Edge：
    "left" "right" "top" "bottom"
    "top-left" "top-right" "bottom-left" "bottom-right"
不在任何边缘时返回 None。
"""
from __future__ import annotations


def hit_test(x: int, y: int, w: int, h: int, margin: int) -> str | None:
    """判断局部坐标 (x, y) 是否落在尺寸 (w, h) 窗口的缩放边缘。

    margin 为边缘命中阈值（像素）。角区域优先于边。
    超出窗口范围或落在内部（距各边都 > margin）时返回 None。
    """
    if w <= 0 or h <= 0 or margin <= 0:
        return None
    # 超出窗口本身不处理
    if x < 0 or y < 0 or x > w or y > h:
        return None

    left = x <= margin
    right = x >= w - margin
    top = y <= margin
    bottom = y >= h - margin

    # 角优先
    if top and left:
        return "top-left"
    if top and right:
        return "top-right"
    if bottom and left:
        return "bottom-left"
    if bottom and right:
        return "bottom-right"
    if left:
        return "left"
    if right:
        return "right"
    if top:
        return "top"
    if bottom:
        return "bottom"
    return None
