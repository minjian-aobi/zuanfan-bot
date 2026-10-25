"""点击落点避让：常驻 UI 控件和虚拟摇杆都不能被点到。

三星气泡可能正好压在资源条、背包、底部按钮条这些常驻控件上，也可能压在
左下角的虚拟摇杆上。那一下点下去会触发控件或被当成推摇杆，而不是点开气泡，
表现成「脚本点了三星，屏幕上三星还在」。

做法是找距原落点最近、且不在任何禁区里的点。禁区两类：一批矩形（UI 控件）
和一个圆（摇杆）。两类放在一起算，所以不会出现「躲开矩形又掉进摇杆」
这种来回推的情况。

搜索用的是按距离排好序的整数偏移表，第一个合法的候选就是全局最近解，
不是沿某个方向推出去的近似解。表只在第一次用到时建，之后常驻内存。

气泡的点击区比图标大，挪几十像素往往仍在气泡上，直接就点到了；就算没点到，
也只是白点一下，下一轮 next_stars 会把三星重新捡回来。

坐标是 960x540 下量出来的像素值。换分辨率要重测。
"""

from __future__ import annotations

import sys

VERBOSE = "--quiet" not in sys.argv

SCREEN = (960, 540)

# 挑替代点时要离禁区边界留这么多像素，避开边缘判定。
# 原先摇杆用的是 5，实测偏少，贴着边点仍会被控件吃掉。
DODGE_MARGIN = 7

# 常驻 UI 控件，格式 (x1, y1, x2, y2)
UI_RECTS: tuple[tuple[int, int, int, int], ...] = (
    (280, 478, 715, 525),   # 底部中间那排按钮
    (909, 483, 951, 523),   # 右下角按钮
    (7, 161, 58, 290),      # 左侧竖排
    (3, 3, 293, 112),       # 左上角头像与资源条
    (289, 0, 778, 44),      # 顶部中间横条
    (880, 2, 953, 78),      # 右上角
    (896, 93, 951, 294),    # 右侧竖排
    (899, 308, 949, 357),   # 右侧下方按钮
)

# 圆形虚拟摇杆。peek 量得圆心 (151, 383)，右边缘 x=234，所以半径 83。
# 落在这个圆内的点击会被游戏当成推摇杆，点不到三星气泡。
JOYSTICK_PT = (151, 383)
JOYSTICK_R = 83

# 搜索半径上限。最坏情况是落点正在摇杆圆心，要走 JOYSTICK_R + DODGE_MARGIN
# 也就是 90 才出得去；最厚的矩形高 109，从中心到边缘约 55。留到 160 有余量。
# 真在这个半径内找不到合法点，说明标定出了错，不应该靠扰动半径来兜。
SEARCH_R = 160

_offsets: list[tuple[int, int]] | None = None


def _in_rects(pt: tuple[int, int], grow: int = 0) -> bool:
    for x1, y1, x2, y2 in UI_RECTS:
        if (x1 - grow <= pt[0] <= x2 + grow
                and y1 - grow <= pt[1] <= y2 + grow):
            return True
    return False


def _in_joystick(pt: tuple[int, int], grow: int = 0) -> bool:
    dx = pt[0] - JOYSTICK_PT[0]
    dy = pt[1] - JOYSTICK_PT[1]
    r = JOYSTICK_R + grow
    return dx * dx + dy * dy <= r * r


def blocked(pt: tuple[int, int]) -> bool:
    """这个点能不能直接点。用原始边界判，不加余量：
    本来就没压在控件上的点不应该因为靠得近而被挪走。
    """
    return _in_rects(pt) or _in_joystick(pt)


def _safe(pt: tuple[int, int]) -> bool:
    """这个点能不能当替代点。要在屏内，并且离所有禁区留足 DODGE_MARGIN。"""
    if not (0 <= pt[0] < SCREEN[0] and 0 <= pt[1] < SCREEN[1]):
        return False
    return not _in_rects(pt, DODGE_MARGIN) and not _in_joystick(pt,
                                                                DODGE_MARGIN)


def _offset_table() -> list[tuple[int, int]]:
    """按距原点远近排好的整数偏移。按顺序试，第一个命中就是最近解。"""
    global _offsets
    if _offsets is None:
        rr = SEARCH_R * SEARCH_R
        pts = [(dx, dy)
               for dx in range(-SEARCH_R, SEARCH_R + 1)
               for dy in range(-SEARCH_R, SEARCH_R + 1)
               if dx * dx + dy * dy <= rr]
        pts.sort(key=lambda p: p[0] * p[0] + p[1] * p[1])
        _offsets = pts
    return _offsets


def dodge(pt: tuple[int, int]) -> tuple[int, int]:
    """落点避开所有禁区，返回距原点最近的合法点。

    原点本来就合法时原样返回，不做任何位移。
    真的找不到（正常标定下不会发生）就按原点点下去，最坏不过白点一下。
    """
    if not blocked(pt):
        return pt

    for dx, dy in _offset_table():
        cand = (pt[0] + dx, pt[1] + dy)
        if _safe(cand):
            if VERBOSE:
                dist = (dx * dx + dy * dy) ** 0.5
                print(f"  三星 @ {pt} 在禁区内，改点 {cand}"
                      f"（挪了 {dist:.0f} 像素）")
            return cand

    if VERBOSE:
        print(f"  落点 {pt} 周围 {SEARCH_R} 像素内没有合法点，按原位置点")
    return pt
