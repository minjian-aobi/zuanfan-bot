"""在一张截图上单独测「谁没饭标识」这一步，不连手机不点屏幕。

用法：
    python tools/probe_purge.py shot.png

打印三组数据：所有拜访标识的命中点、所有饭标识的命中点、
每个拜访标识配到的结果。同时存一张标注图，能直接看出框划在哪里。

标注图里的颜色：
    绿框   拜访标识
    黄框   饭标识
    蓝框   在拜访标识旁边找饭标识的窗口
    红圆   判定没饭、要点去取关的位置

跑完先看标注图。蓝框没圈住饭标识就是 FOOD_DY / FOOD_DX 要调；
红圆不在取关按钮上就是 PURGE_DX 要调。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import cv2

# 这个文件在 tools/ 里。脚本主体搬进了 core/，所以把 core/ 加进搜索路径。
# core 里的 templates/、logs/、shots/ 和 adb 都按项目根目录的相对路径找，
# 所以再把工作目录切到根目录，这样从哪个目录跑都行。
# START_DIR 记下切换前的目录，命令行里给的相对路径按它解析。
ROOT = Path(__file__).resolve().parent.parent
START_DIR = Path.cwd()
sys.path.insert(0, str(ROOT / "core"))
os.chdir(ROOT)

import core
import follow_list as fl

OUT_DIR = Path(__file__).resolve().parent.parent / "shots"


def tpl_size(name):
    """模板的宽高。缺模板时返回 None。"""
    try:
        t = core.template(name)
    except FileNotFoundError:
        return None
    h, w = t.shape[:2]
    return w, h


def box(img, cx, cy, w, h, color, thick=1):
    x0, y0 = int(cx - w // 2), int(cy - h // 2)
    cv2.rectangle(img, (x0, y0), (x0 + w, y0 + h), color, thick)


def main() -> None:
    p = argparse.ArgumentParser(description="在截图上测没饭标识的识别")
    p.add_argument("shot", help="截图路径")
    p.add_argument("--out", default=str(OUT_DIR / "purge_probe.png"),
                   help="标注图存哪")
    args = p.parse_args()

    # 只给文件名时先在 shots/ 里找，跟 peek.py 一个规矩
    shot = Path(args.shot)
    if not shot.is_absolute():
        in_shots = ROOT / "shots" / shot.name
        if shot.parent == Path(".") and in_shots.exists():
            shot = in_shots
        else:
            shot = START_DIR / shot
    img = cv2.imread(str(shot))
    if img is None:
        print(f"读不了 {args.shot}")
        return
    h, w = img.shape[:2]
    print(f"图尺寸 {w}x{h}（标定值是 960x540 下量的，不一样就不准）")

    vs = tpl_size(fl.MARK_VISIT)
    fs = tpl_size(fl.MARK_FOOD)
    if vs is None:
        print(f"缺 templates/{fl.MARK_VISIT}.png，没法测")
        return
    if fs is None:
        print(f"缺 templates/{fl.MARK_FOOD}.png，没法测")
        return
    print(f"{fl.MARK_VISIT} 模板 {vs[0]}x{vs[1]}、"
          f"{fl.MARK_FOOD} 模板 {fs[0]}x{fs[1]}")
    print(f"阀值：拜访 {fl.TH_VISIT}、饭 {fl.TH_FOOD}")
    print(f"窗口：纵 {fl.FOOD_DY}、横 {fl.FOOD_DX}、取关偏移 {fl.PURGE_DX}")

    visits = fl.find_all(img, fl.MARK_VISIT, fl.TH_VISIT)
    foods = fl.find_all(img, fl.MARK_FOOD, fl.TH_FOOD)

    print(f"\n拜访标识 {len(visits)} 个：")
    for s, (x, y) in sorted(visits, key=lambda a: (a[1][1], a[1][0])):
        print(f"   {s:.3f} @ ({x}, {y})")

    print(f"饭标识 {len(foods)} 个：")
    for s, (x, y) in sorted(foods, key=lambda a: (a[1][1], a[1][0])):
        print(f"   {s:.3f} @ ({x}, {y})")

    print("\n逐个配对：")
    no_food = []
    for s, (vx, vy) in sorted(visits, key=lambda a: (a[1][1], a[1][0])):
        hit = [(fx, fy) for _, (fx, fy) in foods
               if vx + fl.FOOD_DX[0] <= fx <= vx + fl.FOOD_DX[1]
               and vy + fl.FOOD_DY[0] <= fy <= vy + fl.FOOD_DY[1]]
        near = None
        for _, (fx, fy) in foods:
            d = abs(fx - vx) + abs(fy - vy)
            if near is None or d < near[0]:
                near = (d, fx - vx, fy - vy)
        off = f"最近的饭标识 dx {near[1]} dy {near[2]}" if near else "图上一个饭标识都没有"
        if hit:
            print(f"   ({vx}, {vy}) 有饭 @ {hit[0]}")
        else:
            print(f"   ({vx}, {vy}) 没饭，要点 ({vx + fl.PURGE_DX}, {vy})，{off}")
            no_food.append((vx, vy))

    pick = fl.first_no_food(img)
    print(f"\n没饭的共 {len(no_food)} 个")
    if pick is None:
        print("这一轮不点任何人，会往下滑一页接着找")
    else:
        print(f"这一轮会取关拜访标识 @ {pick} 那一位，"
              f"点 ({pick[0] + fl.PURGE_DX}, {pick[1]})")

    shown = img.copy()
    for _, (x, y) in visits:
        box(shown, x, y, vs[0], vs[1], (0, 200, 0))
    for _, (x, y) in foods:
        box(shown, x, y, fs[0], fs[1], (0, 220, 220))
    for _, (x, y) in visits:
        x0 = x + fl.FOOD_DX[0]
        x1 = x + fl.FOOD_DX[1]
        y0 = y + fl.FOOD_DY[0]
        y1 = y + fl.FOOD_DY[1]
        cv2.rectangle(shown, (x0, y0), (x1, y1), (255, 120, 0), 1)
    for x, y in no_food:
        cv2.circle(shown, (x + fl.PURGE_DX, y), 8, (0, 0, 255), 2)

    out = Path(args.out)
    out.parent.mkdir(exist_ok=True)
    cv2.imwrite(str(out), shown)
    print(f"标注图存在 {out}")


if __name__ == "__main__":
    main()
