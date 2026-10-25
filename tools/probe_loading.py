"""量加载页的匹配分数，用来核对深色加载页能不能稳定认出来。

用法：
    python tools/probe_loading.py              敲回车开始，采样 15 秒
    python tools/probe_loading.py --secs 25    采样 25 秒

敲了回车之后立刻到模拟器里手动点拜访，让加载页在采样窗口内出现。
每帧打两组分数：
    二值化   现在真正用的那套，只比字形
    灰度     旧做法，底色占分数的大头，留着做对照

跑完把最高分那一帧存成两个文件：
    shots/probe_best.png       整张截图
    shots/probe_best_roi.png   LOADING_ROI 那一小块
"""

from __future__ import annotations

import argparse
import os
import sys
import time
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
import enter_wait

NAMES = ("loading_dark", "loading_white")
OUT_DIR = str(Path(__file__).resolve().parent.parent / "shots")


def crop_roi(img):
    h, w = img.shape[:2]
    x0, y0, x1, y1 = enter_wait.LOADING_ROI
    return img[int(y0 * h):int(y1 * h), int(x0 * w):int(x1 * w)]


def main() -> None:
    p = argparse.ArgumentParser(description="量加载页匹配分数")
    p.add_argument("--secs", type=float, default=15.0, help="采样多少秒")
    args = p.parse_args()

    core.connect()

    shot = core.screenshot()
    roi = crop_roi(shot)
    print(f"区域 {roi.shape[1]} x {roi.shape[0]}")
    for name in NAMES:
        try:
            tpl = enter_wait.loading_tpl(name)
            print(f"{name} 模板 {tpl.shape[1]} x {tpl.shape[0]}")
        except FileNotFoundError as exc:
            print(f"{name} 缺模板：{exc}")

    input("敲回车开始采样，然后马上去模拟器里点拜访：")

    best_score = -1.0
    best_img = None
    t0 = time.time()
    end = t0 + args.secs

    while time.time() < end:
        img = core.screenshot()
        score, pt, name = enter_wait.loading_score(img)
        gray = []
        for n in NAMES:
            try:
                s, _ = core.find(img, n, enter_wait.LOADING_ROI, threshold=0.0)
                gray.append(f"{n} {s:.3f}")
            except FileNotFoundError:
                gray.append(f"{n} 缺模板")
        print(f"t={time.time() - t0:5.1f}  二值化 {score:.3f} ({name}) @ {pt}"
              f"  |  灰度 {'  '.join(gray)}")

        if score > best_score:
            best_score = score
            best_img = img

    if best_img is None:
        print("一帧都没采到")
        return

    os.makedirs(OUT_DIR, exist_ok=True)
    full = os.path.join(OUT_DIR, "probe_best.png")
    part = os.path.join(OUT_DIR, "probe_best_roi.png")
    cv2.imwrite(full, best_img)
    cv2.imwrite(part, crop_roi(best_img))
    print(f"最高分 {best_score:.3f}，已存 {full} 与 {part}")


if __name__ == "__main__":
    main()
