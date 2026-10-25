"""标定工具：抛一张 ADB 截图，存入 shots/。

用法（从哪跑都行，脚本自己锚定项目根目录）：
    python tools/grab.py              存为 shots/shot.png
    python tools/grab.py world        存为 shots/shot_world.png

adb 的找法：先信 PATH（start.bat 跑过的话 PATH 里已经有），
没有再找项目根目录自带的 adb.exe（跟 dll 在一起，直接用）。
"""

import shutil
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

DEVICE = "127.0.0.1:16384"  # MuMu12 默认端口，旧版为 7555
ROOT = Path(__file__).resolve().parent.parent   # tools/ 的上一级就是项目根
SHOT_DIR = ROOT / "shots"

ADB = shutil.which("adb") or str(ROOT / "adb.exe")

stem = f"shot_{sys.argv[1]}" if len(sys.argv) > 1 else "shot"
SHOT_DIR.mkdir(exist_ok=True)
out = SHOT_DIR / f"{stem}.png"

subprocess.run([ADB, "connect", DEVICE], capture_output=True)
raw = subprocess.run(
    [ADB, "-s", DEVICE, "exec-out", "screencap", "-p"],
    capture_output=True,
).stdout

if not raw:
    sys.exit("截图失败：检查 adb 是否在 PATH 或项目根目录、MuMu 是否开启、端口是否正确")

with open(out, "wb") as f:
    f.write(raw)

img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
if img is None:
    sys.exit("截图数据无法解码，先跑 adb kill-server 再重试")

h, w = img.shape[:2]
print(f"已保存 {out}，分辨率 {w} x {h}")
if h > w:
    print("注意：截图是竖屏方向，游戏应为横屏，确认模拟器旋转状态")
