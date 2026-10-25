"""avoid.py 的落点避让测试。不连模拟器，直接：

    python tools/t_avoid.py

改过 UI_RECTS、摇杆坐标或 DODGE_MARGIN 后先跑这个。
它不校具体坐标等于多少，只校三件事：
合法点原样返回、禁区内的点被改成合法点、改后的点是搜索范围内最近的一个。
坐标写死的用例改一次标定就得改一次，民不改比改得好。
"""

import os
import sys
from pathlib import Path

# 这个文件在 tools/ 里。脚本主体搬进了 core/，所以把 core/ 加进搜索路径。
# core 里的 templates/、logs/、shots/ 和 adb 都按项目根目录的相对路径找，
# 所以再把工作目录切到根目录，这样从哪个目录跑都行。
# START_DIR 记下切换前的目录，命令行里给的相对路径按它解析。
ROOT = Path(__file__).resolve().parent.parent
START_DIR = Path.cwd()
sys.path.insert(0, str(ROOT / "core"))
os.chdir(ROOT)

import avoid


def eq(got, want, what):
    assert got == want, f"{what}：得到 {got!r}，应为 {want!r}"


def dist(a, b):
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def brute_nearest(pt):
    """用笨办法算一遍最近的合法点，用来比对 avoid.dodge 的结果。"""
    best = None
    best_d = None
    r = avoid.SEARCH_R
    for dx in range(-r, r + 1):
        for dy in range(-r, r + 1):
            cand = (pt[0] + dx, pt[1] + dy)
            d = dx * dx + dy * dy
            if d > r * r or not avoid._safe(cand):
                continue
            if best_d is None or d < best_d:
                best, best_d = cand, d
    return best


# 禁区判定：摇杆圆心、UI 矩形内部都不能直接点
eq(avoid.blocked(avoid.JOYSTICK_PT), True, "摇杆圆心在禁区内")
for x1, y1, x2, y2 in avoid.UI_RECTS:
    mid = ((x1 + x2) // 2, (y1 + y2) // 2)
    eq(avoid.blocked(mid), True, f"UI 矩形 {(x1, y1, x2, y2)} 中心在禁区内")

# 屏幕中部空地：本来合法的点不得被挪动
for pt in [(400, 300), (600, 200), (250, 350)]:
    if avoid.blocked(pt):
        continue
    eq(avoid.dodge(pt), pt, f"合法点 {pt} 不动")

# 禁区内的点：改后必须合法，而且是最近的那个
cases = [
    avoid.JOYSTICK_PT,             # 正好压在摇杆圆心
    (160, 390),                    # 摇杆圆内偏一点
    (500, 500),                    # 底部按钮条
    (500, 20),                     # 顶部横条
    (920, 330),                    # 右侧下方按钮
    (30, 200),                     # 左侧竖排
]
for pt in cases:
    eq(avoid.blocked(pt), True, f"{pt} 应该在禁区内")
    moved = avoid.dodge(pt)
    assert moved != pt, f"{pt} 在禁区内却没被挪开"
    eq(avoid._safe(moved), True, f"{pt} 改点到 {moved} 仍不安全")
    assert 0 <= moved[0] < avoid.SCREEN[0] and 0 <= moved[1] < avoid.SCREEN[1], \
        f"{pt} 改点到 {moved} 出屏了"
    want = brute_nearest(pt)
    eq(round(dist(pt, moved), 6), round(dist(pt, want), 6),
       f"{pt} 改点后应是最近合法点（笨办法给出 {want}）")

print("OK avoid")
