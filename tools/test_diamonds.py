"""晶钻 OCR 的实跑前测试：不进门不吃饭，只读屏幕上方的晶钻数。

用法：
    python tools/test_diamonds.py              现场截一帧，读一次
    python tools/test_diamonds.py 5            连续读 5 次，间隔 2 秒，看稳不稳
    python tools/test_diamonds.py shot.png     读一张已有截图，不用开模拟器
    python tools/test_diamonds.py shot.png 3   同一张图读 3 次（结果应该完全一样）

文件名不带目录时先在 shots/ 里找，跟 peek.py 一个规矩。

每次都会把送进 OCR 的那块 84x20 小图放大 4 倍存到
shots/diamonds_crop_N.png。读出来的数不对时，打开那张小图肉眼核对：

    小图里根本不是数字    图标找错了位置：看打印的图标分数和坐标，
                        分数远低于 0.9 说明模板或 ICON_ROI 要重裁
    数字被切掉一半        数字区偏了：调 diamonds.py 里的 NUM_W / NUM_H
    小图很清楚但数读错    Tesseract 的锅：多半是字号太小或带描边，
                        可以考虑放大或二值化之后再喂 OCR

测试只读数，不写日志、不动游戏，随便跑。
"""

import os
import sys
import time
from pathlib import Path

# 这个文件在 tools/ 里。脚本主体搬进了 core/，所以把 core/ 加进搜索路径。
# core 里的 templates/、logs/、shots/ 和 adb 都按项目根目录的相对路径找，
# 所以再把工作目录切到根目录，这样从哪个目录跑都行。
# START_DIR 记下切换前的目录，命令行里给的相对路径按它解析。
ROOT = Path(__file__).resolve().parent.parent
START_DIR = Path.cwd()
sys.path.insert(0, str(ROOT / "core"))
os.chdir(ROOT)

import cv2

import core
import diamonds

GAP = 2.0     # 连续读时两次之间隔几秒
ZOOM = 4      # 存盘小图的放大倍数，肉眼核对用


def resolve(name: str) -> Path:
    """文件名不带目录时先在 shots/ 里找。"""
    p = Path(name)
    if p.parent == Path("."):
        in_shots = Path(__file__).resolve().parent.parent / "shots" / p.name
        if in_shots.exists():
            return in_shots
    return p if p.is_absolute() else START_DIR / p


def once(img, tag: str):
    """读一次晶钻数，把小图存盘，返回读到的数（读不出返回 None）。"""
    try:
        score, pt, crop = diamonds.crop_number(img)
    except FileNotFoundError as exc:
        print(f"[{tag}] {exc}")
        return None

    if not pt:
        print(f"[{tag}] 没找到晶钻图标（最高分 {score:.3f}），"
              f"模板或 ICON_ROI 要重裁")
        return None

    print(f"[{tag}] 图标 {score:.3f} @ {pt}，"
          f"数字区 {diamonds.NUM_W - diamonds.NUM_SKIP}x{diamonds.NUM_H}"
          f" 从中心 +{diamonds.NUM_SKIP} 像素起往右")

    if crop is None:
        print(f"[{tag}] 数字区裁出来是空的，图标贴着屏幕边了？")
        return None

    out = Path(__file__).resolve().parent.parent / "shots" / f"diamonds_crop_{tag}.png"
    out.parent.mkdir(exist_ok=True)
    big = cv2.resize(crop, None, fx=ZOOM, fy=ZOOM,
                     interpolation=cv2.INTER_NEAREST)
    cv2.imwrite(str(out), big)

    n = diamonds.ocr_int(crop)
    print(f"[{tag}] OCR 结果：{n}　　小图（放大 {ZOOM} 倍）存到 {out}")
    return n


def main() -> None:
    args = sys.argv[1:]

    # 第一个参数是数字当次数，是文件名当截图路径
    filename = None
    count = 1
    if args and not args[0].isdigit():
        filename = args.pop(0)
    if args and args[0].isdigit():
        count = int(args.pop(0))

    if filename:
        path = resolve(filename)
        img = cv2.imread(str(path))
        if img is None:
            sys.exit(f"读不到 {path}")
        h, w = img.shape[:2]
        print(f"用已有截图 {path}（{w} x {h}），不连模拟器")
        grab = lambda: img          # noqa: E731
    else:
        core.connect()
        grab = core.screenshot

    results = []
    for i in range(count):
        results.append(once(grab(), i + 1))
        if i + 1 < count:
            time.sleep(GAP)

    if count > 1:
        ok = [n for n in results if n is not None]
        print(f"\n{count} 次里读出 {len(ok)} 次，"
              f"不同的值有 {sorted(set(ok)) or '无'}")
        if len(set(ok)) > 1:
            print("同一个画面读出不同的数：OCR 不稳，"
                  "打开小图看看是哪一位在跳")


if __name__ == "__main__":
    main()
