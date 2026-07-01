"""widget/resize_util.hit_test 离线单测（纯函数，不依赖 Qt）。"""
import sys
from pathlib import Path

import pytest

# 把 widget 目录加入导入路径
_WIDGET = Path(__file__).resolve().parents[1] / "widget"
if str(_WIDGET) not in sys.path:
    sys.path.insert(0, str(_WIDGET))

from resize_util import hit_test  # noqa: E402

W, H, M = 400, 300, 6


@pytest.mark.parametrize(
    "x,y,expected",
    [
        (3, 150, "left"),
        (397, 150, "right"),
        (200, 2, "top"),
        (200, 298, "bottom"),
        (2, 2, "top-left"),
        (398, 1, "top-right"),
        (1, 299, "bottom-left"),
        (399, 299, "bottom-right"),
        (200, 150, None),       # 正中，内部
        (200, 100, None),       # 内部
    ],
)
def test_hit_test_directions(x, y, expected):
    assert hit_test(x, y, W, H, M) == expected


def test_hit_test_corner_priority_over_edge():
    # 角区域应返回角而非单边
    assert hit_test(0, 0, W, H, M) == "top-left"


def test_hit_test_out_of_bounds_returns_none():
    assert hit_test(-5, 150, W, H, M) is None
    assert hit_test(450, 150, W, H, M) is None


def test_hit_test_invalid_size():
    assert hit_test(1, 1, 0, 0, M) is None
    assert hit_test(1, 1, W, H, 0) is None
