"""单步验证工具：逐个模板跑分数，打印分数与坐标，并把匹配位置画成图。

实时模式，截当前屏幕再识别：
    python tools/check.py                      测所有模板
    python tools/check.py zuanfan              只测钻饭，含所有变体
    python tools/check.py --shot blue2 zuanfan 同上，但截图存为 shot_blue2.png

离线模式，拿现成图片识别，不连模拟器：
    python tools/check.py --img shot_blue.png zuanfan

不带 --shot 时实时模式存为 shot_check.png，会被下一次覆盖。
要留样本就加 --shot，一次成型，不用先跑 grab.py 再跑 check.py。

产物全在 shots/ 里：
    shots/shot_<名>.png        本次用的原始截图
    shots/debug_shot_<名>.png  匹配结果图

调试图上的颜色：
    红框    CHAT_ROI，钻饭与粉丝字样的搜索范围
    蓝框    VISIT_ROI，拜访按钮的搜索范围
    紫框    ANCHOR_ROI，两个世界频道锚点的搜索范围
    绿框    匹配命中，分数 0.8 以上
    黄框    匹配位置，分数不到 0.8
    青色    由钻饭位置推算出的头像点，十字是点击位置，方框是去重裁剪区

青色十字没落在头像上，就要改 core.py 里的 AVATAR_X_RATIO 或 AVATAR_DY。

模板的搜索范围跟着脚本跑时一致：zuanfan 和 fensi 限在 CHAT_ROI，
btn_visit 限在 VISIT_ROI，anchor_world 与 anchor_world1 限在 ANCHOR_ROI。
这样 check 里的分数和真正跑的时候是同一个口径。

模板拿回它自己的出身截图上测，分数应该接近 1.000。达不到就是模板本身有问题，
达到了而实时分数很低，就是目标当时不在屏幕上。

分数解读（目标确实在屏幕上时）：
    0.85 以上  可靠
    0.75 到 0.85  可用，适当降阈值
    0.60 到 0.75  模板包含了太多背景，重裁
    0.60 以下  分辨率或缩放不对，或当前界面确实没有该元素
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

import cv2

import zuanfan_bot as z

SHOT_DIR = Path(__file__).resolve().parent.parent / "shots"
# 前缀匹配，所以 zuanfan_pink、fensi_pink 这类变体也会限在同一个 ROI 里。
# anchor_world1 必须单独列一条：它不以 anchor_world_ 开头，不算 anchor_world 的变体。
ROI_PREFIXES = {
    "zuanfan": ("CHAT_ROI", z.CHAT_ROI),
    "fensi": ("CHAT_ROI", z.CHAT_ROI),
    "btn_visit": ("VISIT_ROI", z.VISIT_ROI),
    "anchor_world": ("ANCHOR_ROI", z.ANCHOR_ROI),
    "anchor_world1": ("ANCHOR_ROI", z.ANCHOR_ROI),
}


def roi_for(name: str):
    for prefix, (label, roi) in ROI_PREFIXES.items():
        if name == prefix or name.startswith(prefix + "_"):
            return label, roi
    return None, None


def is_zuanfan(name: str) -> bool:
    return name == "zuanfan" or name.startswith("zuanfan_")


def resolve(name: str) -> Path:
    p = Path(name)
    if p.parent == Path("."):
        in_shots = SHOT_DIR / p.name
        if in_shots.exists():
            return in_shots
    return p if p.is_absolute() else START_DIR / p


args = sys.argv[1:]
img_path = None
shot_name = "check"

while args and args[0].startswith("--"):
    flag = args.pop(0)
    if flag == "--img":
        if not args:
            sys.exit("--img 后面要跟一个图片路径")
        img_path = resolve(args.pop(0))
    elif flag == "--shot":
        if not args:
            sys.exit("--shot 后面要跟一个名字，比如 --shot blue2")
        shot_name = args.pop(0)
    else:
        sys.exit(f"不认识的参数 {flag}")

SHOT_DIR.mkdir(exist_ok=True)

if img_path:
    img = cv2.imread(str(img_path))
    if img is None:
        sys.exit(f"读不到 {img_path}")
    src = str(img_path)
    stem = img_path.stem
else:
    z.connect()
    img = z.screenshot()
    stem = f"shot_{shot_name}"
    shot = SHOT_DIR / f"{stem}.png"
    cv2.imwrite(str(shot), img)
    src = f"当前屏幕（已存为 {shot}）"

h, w = img.shape[:2]
print(f"{src}   {w} x {h}\n")

# 传了名字时把它当前缀，把所有变体都列出来
if args:
    names = []
    for a in args:
        matched = sorted(
            p.stem
            for p in z.TPL_DIR.glob(f"{a}*.png")
            if p.stem == a or p.stem.startswith(a + "_")
        )
        names.extend(matched or [a])
else:
    names = sorted(p.stem for p in z.TPL_DIR.glob("*.png"))

if not names:
    sys.exit("templates/ 里还没有模板图")

debug = img.copy()


def draw_roi(roi, color):
    x1, y1, x2, y2 = roi
    cv2.rectangle(
        debug,
        (int(x1 * w), int(y1 * h)),
        (int(x2 * w) - 1, int(y2 * h) - 1),
        color,
        1,
    )


draw_roi(z.CHAT_ROI, (0, 0, 255))      # 红
draw_roi(z.VISIT_ROI, (255, 0, 0))     # 蓝
draw_roi(z.ANCHOR_ROI, (255, 0, 255))  # 紫

best = (0.0, "", None)
for name in names:
    label, roi = roi_for(name)
    try:
        score, pt = z.find(img, name, roi or z.FULL, threshold=0.0)
    except FileNotFoundError as exc:
        print(f"{name:16s} {exc}")
        continue
    tpl = z.template(name)
    th, tw = tpl.shape[:2]
    mode = ""
    if name in z.BINARY_TPLS:
        mode = f"  二值化 {z.BINARY_TH}"
    elif name in z.COLOR_TPLS:
        mode = "  彩色"
    tag = f"  (限 {label})" if label else ""
    print(
        f"{name:16s} 分数 {score:.3f}   位置 {pt}   "
        f"模板尺寸 {tw}x{th}{tag}{mode}"
    )
    if score > best[0]:
        best = (score, name, pt)

    if not pt:
        continue
    # 分数高画绿，分数低画黄，方便一眼分辨
    color = (0, 255, 0) if score >= 0.8 else (0, 200, 255)
    x, y = pt
    cv2.rectangle(
        debug,
        (x - tw // 2, y - th // 2),
        (x - tw // 2 + tw, y - th // 2 + th),
        color,
        1,
    )
    cv2.putText(
        debug,
        f"{name} {score:.2f}",
        (max(0, x - tw // 2), max(10, y - th // 2 - 3)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.35,
        color,
        1,
    )

# 最高分是钻饭时，把推算出的头像点画出来，用于验证 AVATAR_DY
if best[2] and is_zuanfan(best[1]):
    ax = int(z.AVATAR_X_RATIO * w)
    ay = best[2][1] + z.AVATAR_DY
    side = int(z.AVATAR_CROP * w)
    cyan = (255, 255, 0)
    cv2.line(debug, (ax - 8, ay), (ax + 8, ay), cyan, 1)
    cv2.line(debug, (ax, ay - 8), (ax, ay + 8), cyan, 1)
    cv2.rectangle(
        debug,
        (ax - side // 2, ay - side // 2),
        (ax + side // 2, ay + side // 2),
        cyan,
        1,
    )
    cv2.putText(
        debug,
        f"avatar ({ax},{ay})",
        (max(0, ax - side // 2), max(10, ay - side // 2 - 3)),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.35,
        cyan,
        1,
    )
    print(
        f"\n推算头像点 ({ax}, {ay})   "
        f"来自 {best[1]} @ {best[2]}，AVATAR_DY = {z.AVATAR_DY}"
    )
    print("青色十字没落在头像上就要改 AVATAR_X_RATIO 或 AVATAR_DY")

out = SHOT_DIR / f"debug_{stem}.png"
cv2.imwrite(str(out), debug)
if len(names) > 1:
    print(f"\n最高分：{best[1]} {best[0]:.3f}")
print(f"匹配结果已画到 {out}，红框 CHAT_ROI，蓝框 VISIT_ROI，紫框 ANCHOR_ROI")

