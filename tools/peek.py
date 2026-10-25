"""标定工具：读坐标、读 ROI、框选直接存模板图。

用法：
    python tools/peek.py                      读 shots/shot.png，原尺寸显示
    python tools/peek.py shot_world.png       读 shots/shot_world.png
    python tools/peek.py shot_world.png 2     2 倍显示

文件名不带目录时先在 shots/ 里找，找不到再当作当前目录的路径。

操作：
    左键单击      打印点坐标与画面比例
    左键拖拽      框选，松手后打印矩形与 ROI 四元组
    s             把当前矩形存成模板图，文件名在命令行里输
    r             清除当前矩形
    q 或 Esc      退出，点窗口的 X 也能退

放大只影响显示，坐标、ROI、保存的模板全部取自原图像素，不会失真。
"""

import sys
from pathlib import Path

import cv2

WIN = "peek"
TPL_DIR = Path(__file__).resolve().parent.parent / "templates"
SHOT_DIR = Path(__file__).resolve().parent.parent / "shots"


def resolve(name: str) -> Path:
    p = Path(name)
    if p.parent == Path("."):
        in_shots = SHOT_DIR / p.name
        if in_shots.exists():
            return in_shots
    return p


path = resolve(sys.argv[1] if len(sys.argv) > 1 else "shot.png")
scale = float(sys.argv[2]) if len(sys.argv) > 2 else 1.0

base = cv2.imread(str(path))
if base is None:
    sys.exit(f"读不到 {path}，先跑 grab.py")

H, W = base.shape[:2]
DW, DH = int(W * scale), int(H * scale)
# 最邻插值，放大后像素边界清楚，方便对齐
view = base if scale == 1.0 else cv2.resize(
    base, (DW, DH), interpolation=cv2.INTER_NEAREST
)

print(f"{path}   原图 {W} x {H}   显示 {DW} x {DH}（{scale} 倍）")
print("左键拖拽框选，s 存模板，r 清除，q 退出\n")

st = {"drag": False, "p0": (0, 0), "cur": (0, 0), "rect": None}


def to_src(pt):
    x = min(max(int(round(pt[0] / scale)), 0), W - 1)
    y = min(max(int(round(pt[1] / scale)), 0), H - 1)
    return x, y


def on_mouse(event, x, y, flags, param):
    st["cur"] = (x, y)
    if event == cv2.EVENT_LBUTTONDOWN:
        st["drag"] = True
        st["p0"] = (x, y)
        st["rect"] = None
    elif event == cv2.EVENT_LBUTTONUP:
        st["drag"] = False
        x0, y0 = to_src(st["p0"])
        x1, y1 = to_src((x, y))
        if abs(x1 - x0) < 2 and abs(y1 - y0) < 2:
            print(f"点    ({x0}, {y0})    比例 ({x0 / W:.4f}, {y0 / H:.4f})")
        else:
            a, b = sorted((x0, x1))
            c, d = sorted((y0, y1))
            st["rect"] = (a, c, b, d)
            print(
                f"矩形  ({a}, {c}) - ({b}, {d})    尺寸 {b - a} x {d - c}    "
                f"ROI = ({a / W:.4f}, {c / H:.4f}, {b / W:.4f}, {d / H:.4f})"
            )


def save_rect():
    if not st["rect"]:
        print("先拖一个矩形再按 s")
        return
    a, c, b, d = st["rect"]
    print("模板文件名（不带 .png，直接回车取消）：", end="", flush=True)
    try:
        name = input().strip()
    except EOFError:
        return
    if not name:
        print("已取消")
        return
    TPL_DIR.mkdir(exist_ok=True)
    out = TPL_DIR / f"{name}.png"
    cv2.imwrite(str(out), base[c:d, a:b])
    print(f"已保存 {out}    {b - a} x {d - c}")


cv2.namedWindow(WIN, cv2.WINDOW_AUTOSIZE)
cv2.setMouseCallback(WIN, on_mouse)

while True:
    canvas = view.copy()

    # 十字准线
    cx, cy = st["cur"]
    cv2.line(canvas, (cx, 0), (cx, DH), (120, 120, 120), 1)
    cv2.line(canvas, (0, cy), (DW, cy), (120, 120, 120), 1)

    # 拖拽中的实时矩形
    if st["drag"]:
        cv2.rectangle(canvas, st["p0"], st["cur"], (0, 255, 0), 1)
    elif st["rect"]:
        a, c, b, d = st["rect"]
        cv2.rectangle(
            canvas,
            (int(a * scale), int(c * scale)),
            (int(b * scale), int(d * scale)),
            (0, 200, 255),
            1,
        )

    # 顶部提示条
    sx, sy = to_src(st["cur"])
    label = f"({sx}, {sy})  ratio ({sx / W:.3f}, {sy / H:.3f})"
    if st["rect"]:
        a, c, b, d = st["rect"]
        label += f"   rect {b - a}x{d - c}  press s to save"
    cv2.rectangle(canvas, (0, 0), (DW, 18), (0, 0, 0), -1)
    cv2.putText(
        canvas, label, (5, 13), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (255, 255, 255), 1
    )

    cv2.imshow(WIN, canvas)

    key = cv2.waitKey(20) & 0xFF
    if key in (ord("q"), 27):
        break
    if key == ord("s"):
        save_rect()
    if key == ord("r"):
        st["rect"] = None
        print("已清除矩形")

    # 窗口被点 X 关掉时退出循环
    try:
        if cv2.getWindowProperty(WIN, cv2.WND_PROP_VISIBLE) < 1:
            break
    except cv2.error:
        break

cv2.destroyAllWindows()
