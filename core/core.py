"""奥比岛钻饭脚本的底层部件：标定值、adb、模板匹配、去重、日志、单步动作。

这里只放“怎么做一件事”，不放“一轮该做哪些事”。一轮的编排、档位判断与主循环
在 zuanfan_bot.py，档位定义在 modes.py，界面在 panel.py，点拜访之后的等待与
进门扫描在 enter_wait.py，点击落点避让在 avoid.py。入口仍然是
zuanfan_bot.py 和 panel.py，不要直接跑这个文件。

拆开是为了让单个文件保持在好读、好改的长度。

基准分辨率 960x540。分辨率变化后所有模板图必须重裁，标定值也要重测。

三处固定坐标：公屏入口、抓虫按钮、头像横坐标。
这三个位置在界面上不动。
拜访按钮、三星气泡、撒花按钮、关注按钮的位置会变或依赖
弹窗时机，只能靠图像识别。

catch_all_bugs 的退出原因（日志 exit 列）：
    clean    三星和撒花按钮都不在了，吃到了
    max      抓满 STARS_MAX 次还看得见三星，一直没吃上
    back     中途被弹回世界频道，无法确认，按没吃到算
    blocked  撒花界面点不掉，三星可能一直被挡着，也按没吃到算
"""

from __future__ import annotations

import csv
import subprocess
import sys
import time
from collections import deque
from pathlib import Path

import cv2
import numpy as np

import modes
import avoid

# ==================== 配置 ====================

ADB = "adb"
DEVICE = "127.0.0.1:16384"
TPL_DIR = Path("templates")

# ROI 用画面比例表示，与分辨率无关。格式 (x1, y1, x2, y2)
FULL = (0.0, 0.0, 1.0, 1.0)

# 以下值已在 960x540 下标定
# 聊天面板的消息区，原图像素 (125, 38) - (424, 479)
CHAT_ROI = (0.1302, 0.0704, 0.4417, 0.8870)

# 拜访按钮的横坐标固定，两张截图实测中心 x 都是 217（比例 0.2260）。
# 纵向位置随头像变，同一张卡在两次实测里分别落在 y=375 和 y=411，
# 所以只收窄横向，纵向必须留全高。
# 窗口宽 115 像素，模板宽 35 像素，余量够大。
VISIT_ROI = (0.17, 0.0, 0.29, 1.0)

AVATAR_X_RATIO = 0.1094  # 头像中心 x = 105 / 960
AVATAR_DY = -20          # 头像中心 y 比钻饭文字中心 y 高 20 像素
AVATAR_CROP = 0.045      # 去重用的头像裁剪边长，画面比例，约 43 像素

TAB_WORLD_PT = (501, 503)  # 公屏入口，位置固定
CATCHBUG_PT = (806, 404)   # 抓虫按钮，位置固定

# 有时公屏入口上面压着一个面板，怕不是点不开，而是这一下被那个面板接走了。
# 先点这个位置把面板点掉，再点公屏入口就能开。这个坐标与 follow_list 里
# NEXT_ALT_PT 是同一个点，那边点坐标 C 不动时也靠它解卡。
WORLD_CLOSE_PT = (648, 140)
WORLD_CLOSE_GAP = 0.5      # 点掉遮挡之后等多久再点公屏入口

# 在不在别人家派对里的判据：在 PARTY_ROI 这块里找「下一家」按钮
# （xiayijia 模板）。「下一家」只在开派对的人家里出现，普通家园没有。
# 外圈矩形 (861, 29)-(931, 61) 是按钮可能出现的范围，里面那个
# (870, 34)-(920, 55) 是模板本身的选区，已存成 templates/xiayijia.png。
# 认派必须用这个按钮，不能用顶部的蓝色晶钻图标：晶钻图标在自己家、
# 别人家都常驻，拿它当「在别人家」的判据连自己家里也会命中。
# 识别到派对后回家要改点左上角返回（follow_list.HOME_PT），
# 右下角那两下在派对里点不动。
PARTY_TPL = "xiayijia"
PARTY_ROI = (0.8969, 0.0537, 0.9698, 0.1130)

# 撒花按钮只会出现在这个区域里。矩形 (403, 311) - (562, 385)。
# 公屏点不开时优先在这个区域里找撒花按钮——卡住的常见原因就是
# 派对家园的撒花界面没点掉，压在公屏入口上面。
FLOWER_ROI = (0.4198, 0.5759, 0.5854, 0.7130)

# 虚拟摇杆与常驻 UI 控件的禁区都放在 avoid.py 里，点击前过一遍 avoid.dodge。

# 默认匹配阈值。钻饭文字、世界频道锚点、拜访按钮、撒花按钮、
# 粉丝字样、关注按钮、取关按钮都用这个。
# 实测依据：每种气泡皮肤都有自己的模板，在对应皮肤上都能到 1.000，
# 拜访按钮 0.980，锚点 1.000。无目标画面上钻饭的噪声峰值是 0.558 和 0.626。
TH_DEFAULT = 0.85

# 1 号频道判定单独用一个更严的阈值。
# anchor_world1 模板带的“1”只占整块图的一小部分，其他频道的锚点跟它
# 只差一个字符，分数天然偏高（旧模板实测非 1 号频道上跑到过 0.973）。
# 用 TH_DEFAULT 的 0.85 会把别的频道认成 1 号，那样 switch_channel1
# 相当于开空车，重启后可能停在别的频道干活。
# 两类错的代价不对称：误判“已在 1 号”是硬伤，误判“不在 1 号”只是多
# 切一次频道，几秒钟。所以这个阈值宁高不宁低。
# 实测依据：当前模板在 1 号频道上能稳定到 1.000，留 0.08 的余量够用。
TH_WORLD1 = 0.92

# 三星气泡阈值，比默认略松。三星气泡是半透明的，底下透出的家具每家不同，
# 实测一张新家园图上只有 0.853。无目标画面上的噪声峰值是 0.558 和 0.626，
# 所以 0.80 距噪声底还有余量，距那张 0.853 的家园图只剩 0.05。
# 两类错的代价不对称：漏看一家永久少吃一顿，误命中只是白点两下，
# 接着 ensure_world 就回公屏了。如果发现开始漏吃，先把这个值降回 0.70。
TH_STARS = 0.80

COLOR_TPLS = {"three_stars"}   # 彩色匹配，靠颜色区分三星与二星。按前缀生效
                               # enter_wait 在导入时追加 loading

# 二值化匹配。只适用于高对比场景，比如深底浅字的蓝色钻饭气泡。
# btn_visit 试过，反而更差：粉色资料卡上白字和粉底的灰度都在高位，
# 阈值 170 一切下去字和底一起变白，字形轮廓消失，分数从 0.980 降到 0.658。
BINARY_TPLS: set[str] = set()
BINARY_TH = 170                # 二值化阈值。启用时在 140 到 200 之间调
# 其余模板转灰度

DEDUP_SIZE = 200         # 去重集合保留的最近记录数
TIMEOUT_CARD = 3.0       # 点头像后等资料卡
TIMEOUT_TARGET = 30.0    # 公屏上找不到钻饭时最多等这么久（只在频道确实
                         # 开着的时候计时），超时就刷新公屏
TARGET_POLL = 0.1        # 等钻饭那一轮截一帧的间隔。比 POLL 密：
                         # adb 截图本身就要零点几秒，帧间隔再睡 0.3 的话，
                         # 消息冒头到被发现最多要慢将近一秒
WORLD_REOPEN_MAX = 2     # 轮询期间发现锚点不在时，最多重新点开公屏几次
POP_WAIT = 0.5           # 点三星后等抓虫按钮弹出。0.1 秒太短，按钮常常还没出来就被盲点了
BUG_WAIT = 0.1           # 点抓虫后等弹窗退场，再决定要不要抓下一只
STARS_MAX = 6            # 同一个家园里最多点几次三星气泡。防假峰死循环
BUG_MAX = 2              # 除虫按钮最多点几次。气泡可能反复撞出假峰，
                         # 但除虫点多了没意义：正常一口一次就吃上，
                         # 两次还没吃掉多半是没饭了。气泡照点，除虫省掉
FLOWER_MAX = 3           # 抓虫循环里撒花最多点几次。超过说明点不掉，不再重试。
                         # 进门那一段的撒花上限归 enter_wait.FLOWER_TAPS_MAX
FLOWER_DELAY = 0.5       # 点开三星后若又出现撒花按钮，点掉它再等这么久才抓虫
FOLLOW_WAIT = 0.2        # 点关注后等按钮文字从关注变成取关。要是出现
                         # 点完关注找不到拜访按钮的情况，把这个调回 0.5
FANS_BAND = 14           # 粉丝字样与钻饭算同一行的纵向容差，单位像素
POLL = 0.3               # 轮询间隔
IDLE_POLL = 0.3          # 界面按了暂停时主循环的空转间隔
NOTE_GAP = 3.0           # 等钻饭时每隔这么久往终端报一声，别每帧都刷

# 单次 adb 调用的上限。原先不设上限，subprocess.run 会无期限阻塞：
# 8/25 日志里有一轮 home_secs 跑到 84.8 秒，而逻辑上限只有 17.5 秒，
# 多出来的一分多钟就是卡在 adb 里。
# 日志只在一轮跑完时写，所以卡住期间从外面看就像脚本没在运行。
# 超时抛 subprocess.TimeoutExpired，主循环兜住后照常跑下一轮。
ADB_TIMEOUT_SHOT = 10.0  # 截图。正常几百毫秒
ADB_TIMEOUT_TAP = 5.0    # 点击等短命令

# 连续多少轮 no_world 就报警。游戏掉线时一轮只花几秒，会闷着刷几百轮：
# 8/25 那晕连续 873 轮空转了一小时四十三分钟，靠事后翻日志才发现。
NO_WORLD_ALERT = 5
NO_WORLD_ALERT_EVERY = 50  # 首次报警后每隔这么多轮再提醒一次，免得刷屏
STUCK_DIR = Path("shots")  # 报警时把当时的画面存这里

# 卡死自愈：关掉游戏重开，再自己点回世界频道 1。
# 8/26 那份日志里 01:43:54 起连续 59 轮 no_world，每轮 6 秒，空转了六分钟。
# 手动处理就是关掉游戏重进，再点几下登录，所以这套动作可以自己走。
GAME_PKG = "com.leiting.aobi"   # 固定值，跟安装包绑定
NO_WORLD_RESTART = 20    # 连续这么多轮 no_world 就重启游戏。一轮约 6 秒，合两分钟
RESTART_MAX = 3          # 连续重启失败几次后不再重试，免得无人看着时反复重启
KILL_WAIT = 3.0          # force-stop 后等进程真的退干净
BOOT_WAIT = 15.0         # 拉起后先死等。游戏加载很慢，这段时间看不到锚点是正常的
ENTER_PT = (851, 53)     # 进游戏要点的位置
ENTER_GAP = 1.0          # 上面那个位置点两下，中间隔这么久
WORLD_TAP_GAP = 2.5      # 反复点公屏入口时每下之间的间隔。实测公屏面板加载约 1.5 秒，
                         # 留到 2.5 秒。点得比这快，第二下会把刚打开的面板又关掉，
                         # 或者在面板还没画出来时就去截图，白等一整轮
TIMEOUT_WORLD_OPEN = 30.0   # 平时点开公屏的总上限
TIMEOUT_WORLD_BOOT = 60.0   # 刚重启完点开公屏的总上限，给加载留足时间
SWITCH_PT = (350, 21)           # 公屏上的“切换”
CHANNEL1_PT = (497, 102)        # 频道列表里的“1”
CONFIRM_SWITCH_PT = (705, 232)  # “确认切换”
SWITCH_GAP = 1.0         # 切频道这三下之间的间隔
TIMEOUT_CHANNEL = 10.0   # 点完确认后等 1 号频道锚点
CHANNEL_RETRY = 2        # 切频道最多试几轮

# 两个锚点都只会出现在左上角固定位置，没必要全屏搜。实测矩形是
# (131, 8) 到 (334, 31)，这里放到 (128, 0) 到 (340, 40)，
# 留几像素到十几像素的容错空间防漂移。搜小区域还能少踩假峰。
# 注意区域必须比模板大：宽 212、高 40，比最宽的锚点模板（117x21）宽裕。
ANCHOR_ROI = (0.1333, 0.0, 0.3542, 0.0741)

# 日志。每轮一行，按天一个文件，字段固定，可以直接丢进表格算平均值。
# 只记结果不记截图，跑一整天也就几十 KB。
# mode 放在最后一列，这样加它之前的旧文件用旧表头读，前十列仍能对上。
LOG_DIR = Path("logs")
LOG_FIELDS = ("time", "result", "skin", "fan", "followed",
              "taps", "exit", "ate", "world_secs", "home_secs", "mode")
LOG_KEEP = 3             # 日志目录里只保留最近这么多个文件，更早的自动删掉

# 游戏的一天从凌晨 5 点开始，不是 0 点。0 点到 5 点之间跑的脚本，
# 吃的还是前一天的额度，日志也要记到前一天名下。所有按天命名的文件
# （run_*.csv、diamonds_*.csv、purge_*.flag）统一走 game_day()，
# 改界限只改这一个数。
GAME_DAY_RESET_HOUR = 5


# ==================== 控制台颜色 ====================

# 晶钻相关的打印用蓝色，跟刷饭的过程输出分开，扫一眼就知道晶钻状态。
# Windows 控制台默认不认 ANSI 颜色码，要用 ctypes 打开
# ENABLE_VIRTUAL_TERMINAL_PROCESSING（0x0004）。Win10 以后的
# cmd / PowerShell / Windows Terminal 都支持；开不了、或输出被
# 重定向到文件时退回无色，不会在文件里留下控制字符。
def _enable_ansi() -> bool:
    """给控制台打开 ANSI 颜色支持。开不了返回 False。"""
    if not sys.stdout.isatty():
        return False
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        handle = k32.GetStdHandle(-11)           # STD_OUTPUT_HANDLE
        mode = ctypes.c_ulong()
        if not k32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        return bool(k32.SetConsoleMode(handle, mode.value | 0x0004))
    except (AttributeError, OSError):
        return False


ANSI = _enable_ansi()
C_BLUE = "\033[94m" if ANSI else ""   # 亮蓝：黑底上纯蓝（34）太暗看不清
C_RESET = "\033[0m" if ANSI else ""


def print_blue(text: str) -> None:
    """晶钻相关的打印，蓝色显示。控制台不支持颜色时就是普通 print。"""
    print(f"{C_BLUE}{text}{C_RESET}")


def game_day() -> str:
    """今天的游戏日，形如 20260928。凌晨 RESET 点之前算前一天。

    实现上就是把当前时间往回拨 RESET 小时再取日期。
    """
    t = time.time() - GAME_DAY_RESET_HOUR * 3600
    return time.strftime("%Y%m%d", time.localtime(t))

DEBUG = "--debug" in sys.argv  # 加 --debug 运行时打印每次匹配的分数与坐标
# 过程提示默认开着：无人看守地跑时，终端上得能看出脚本卡在哪一步。
# 只有匹配分数那种每帧一行的输出才归 --debug。加 --quiet 可以全部关掉。
VERBOSE = "--quiet" not in sys.argv
_ALT_WARNED = False   # 备用日志文件的提示只打一次，别每轮都刷

# ==================== ADB ====================


def sh(*args: str, timeout: float = ADB_TIMEOUT_TAP) -> bytes:
    """跑一条 adb 命令。超时抛异常，由主循环兜住。

    一定要给 timeout：不给的话 adb 挂住时这里会一直等，整个脚本静静停死，
    日志上只留下一段空白。
    """
    return subprocess.run([ADB, "-s", DEVICE, *args],
                          capture_output=True, timeout=timeout).stdout


def connect() -> None:
    subprocess.run([ADB, "connect", DEVICE], capture_output=True)


def screenshot() -> np.ndarray:
    raw = sh("exec-out", "screencap", "-p", timeout=ADB_TIMEOUT_SHOT)
    img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError("截图失败，检查 ADB 连接")
    return img


def tap(x: int, y: int) -> None:
    """点一下 (x, y)。每次点击都打印坐标：盯着控制台就能知道脚本
    正在干嘛、点在了哪，出问题时也能对上最后一个动作是什么。"""
    print(f"  点击 ({int(x)}, {int(y)})")
    sh("shell", "input", "tap", str(int(x)), str(int(y)))


# ==================== 模板匹配 ====================

_cache: dict[str, np.ndarray] = {}


def is_color(name: str) -> bool:
    """这个模板要不要走彩色匹配。按前缀判，所以 three_stars_xxx 也算彩色。"""
    return any(name == p or name.startswith(p + "_") for p in COLOR_TPLS)


def prep(img: np.ndarray, name: str) -> np.ndarray:
    """按模板的匹配模式预处理图像。模板和截图必须过同一道处理。

    三种模式：
        彩色    目标靠颜色区分，如三星与二星
        二值化  高对比的深底浅字或浅底深字，只保留字形轮廓
        灰度    其余。低对比目标必须用这个，灰度保留了完整的明暗层次
    """
    if is_color(name):
        return img
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    if name in BINARY_TPLS:
        _, bw = cv2.threshold(gray, BINARY_TH, 255, cv2.THRESH_BINARY)
        return bw
    return gray


def template(name: str) -> np.ndarray:
    if name not in _cache:
        path = TPL_DIR / f"{name}.png"
        flag = cv2.IMREAD_COLOR if is_color(name) else cv2.IMREAD_GRAYSCALE
        img = cv2.imread(str(path), flag)
        if img is None:
            raise FileNotFoundError(f"缺少模板图 {path}")
        _cache[name] = prep(img, name)
    return _cache[name]


def variants(prefix: str) -> list[str]:
    """同一目标的所有模板变体。

    聊天气泡有多种皮肤，同样的字在不同底色下灰度分布不同，
    一个模板盖不住。把每种皮肤各裁一张，命名为
    zuanfan.png、zuanfan_pink.png、zuanfan_blue.png，全部会被试一遍。

    三星气泡同理，命名为 three_stars.png、three_stars_xxx.png。
    粉丝字样同理，命名为 fensi.png、fensi_pink.png、fensi_blue.png。

    注意：带下划线的名字会被当成变体，所以 1 号频道锚点叫 anchor_world1，
    不能叫 anchor_world_1，否则会被当成 anchor_world 的一种皮肤。
    同理关注与取关是两个独立目标，btn_follow 和 btn_unfollow 不构成变体关系，
    因为 btn_unfollow 不是以 btn_follow_ 开头的。
    """
    names = sorted(
        p.stem
        for p in TPL_DIR.glob(f"{prefix}*.png")
        if p.stem == prefix or p.stem.startswith(prefix + "_")
    )
    if not names:
        raise FileNotFoundError(f"缺少模板图 {TPL_DIR / (prefix + '*.png')}")
    return names


def match_one(img, name, roi):
    """在 roi 区域内匹配单个模板，返回 (score, center)。"""
    tpl = template(name)
    src = prep(img, name)
    h, w = src.shape[:2]
    x1, y1, x2, y2 = roi
    ox, oy = int(x1 * w), int(y1 * h)
    region = src[oy:int(y2 * h), ox:int(x2 * w)]

    if region.shape[0] < tpl.shape[0] or region.shape[1] < tpl.shape[1]:
        return 0.0, None

    res = cv2.matchTemplate(region, tpl, cv2.TM_CCOEFF_NORMED)
    _, score, _, loc = cv2.minMaxLoc(res)
    th, tw = tpl.shape[:2]
    return score, (ox + loc[0] + tw // 2, oy + loc[1] + th // 2)


def find(img, name, roi=FULL, threshold=TH_DEFAULT):
    """匹配单个模板。命中返回 (score, (cx, cy))，未命中返回 (score, None)。"""
    score, center = match_one(img, name, roi)
    if DEBUG:
        print(f"  find {name:16s} {score:.3f} @ {center}")
    if score < threshold:
        return score, None
    return score, center


def find_any(img, prefix, roi=FULL, threshold=TH_DEFAULT):
    """把 prefix 的所有变体试一遍，取最高分。返回 (score, center, name)。"""
    best = (0.0, None, "")
    for name in variants(prefix):
        score, center = match_one(img, name, roi)
        if DEBUG:
            print(f"  find {name:16s} {score:.3f} @ {center}")
        if score > best[0]:
            best = (score, center, name)
    if best[0] < threshold:
        return best[0], None, best[2]
    return best


def wait_for(name, timeout, roi=FULL, threshold=TH_DEFAULT):
    """轮询直到命中或超时。返回 (截图, 中心点)。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        img = screenshot()
        _, pt = find(img, name, roi, threshold)
        if pt:
            return img, pt
        time.sleep(POLL)
    return None, None


def in_world(img) -> bool:
    """世界频道锚点在不在画面上。不区分几号频道。

    只能用来判定「还没有离开世界频道」。锚点不在不等于已经到了别人家，
    加载中的过渡画面上它也不在。
    """
    return find(img, "anchor_world", ANCHOR_ROI)[1] is not None


def in_world1(img) -> bool:
    """是不是正在世界频道 1。

    anchor_world1 模板带着后面的“1”，anchor_world 不带，所以在 1 号频道上
    两个模板都会命中，在其他频道只有 anchor_world 命中。

    阈值用 TH_WORLD1 而不是默认的 0.85，原因见 TH_WORLD1 的注释。
    """
    return find(img, "anchor_world1", ANCHOR_ROI, TH_WORLD1)[1] is not None


# ==================== 去重 ====================


class Dedup:
    """基于头像图像哈希的近期去重。头像相同的不同玩家会被误判为同一人。"""

    def __init__(self, size: int) -> None:
        self.queue: deque[str] = deque(maxlen=size)
        self.seen: set[str] = set()

    def check_and_add(self, key: str) -> bool:
        if key in self.seen:
            return True
        if len(self.queue) == self.queue.maxlen:
            self.seen.discard(self.queue[0])
        self.queue.append(key)
        self.seen.add(key)
        return False


def dhash(patch: np.ndarray) -> str:
    gray = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
    small = cv2.resize(gray, (9, 8), interpolation=cv2.INTER_AREA)
    bits = (small[:, 1:] > small[:, :-1]).flatten()
    return "".join("1" if b else "0" for b in bits)


# ==================== 日志 ====================


def clean_logs(keep: int = LOG_KEEP) -> None:
    """只留下最新的 keep 个日志文件，更早的删掉。

    清理四种按天命名的文件：run_*.csv、purge_*.flag、
    diamonds_start_*.txt、diamonds_*.csv（不含账号名的旧格式也一起收）。
    按文件修改时间排序，不按文件名，这样 _alt 文件也能排进来。
    只在新建一个日志文件时跑一次，也就是一天一次，不占时间。

    删文件失败不能打断跑圈，所以整段包在 try 里。Excel 正开着某个
    旧文件时删不掉，跳过它，下次再删。
    """
    patterns = ("run_*.csv", "purge_*.flag",
                "diamonds_start_*.txt", "diamonds_*.csv")
    for pattern in patterns:
        try:
            files = sorted(LOG_DIR.glob(pattern),
                           key=lambda p: p.stat().st_mtime, reverse=True)
        except OSError:
            continue

        for old in files[keep:]:
            try:
                old.unlink()
            except OSError as exc:
                if VERBOSE:
                    print(f"  删不掉旧日志 {old.name}：{exc}")
                continue
            if VERBOSE:
                print(f"  删掉旧日志 {old.name}，只留最近 {keep} 个")


def log_row(result: str, detail: dict) -> None:
    """把一轮的结果追加到当天的 csv。

    写盘失败不能打断循环，所以整段包在 try 里。
    编码用 utf-8-sig，Excel 直接双击打开不会乱码。

    Excel 打开 csv 时会独占锁住文件，此时每一轮都会撞 PermissionError。
    这种情况不能只打一行提示就把数据丢掉：改写同目录下的 _alt 文件，
    等 Excel 关掉之后手动合并即可。提示只打第一次，否则每轮刷一行。

    新建一个日志文件时顺手跑一次 clean_logs，把早于最近 LOG_KEEP 个的删掉。
    """
    global _ALT_WARNED

    row: dict = {"time": time.strftime("%H:%M:%S"), "result": result}
    row.update(detail)
    stamp = game_day()
    path = LOG_DIR / f"run_{stamp}.csv"

    def write(target) -> None:
        header = not target.exists()
        with target.open("a", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=LOG_FIELDS,
                                    extrasaction="ignore")
            if header:
                writer.writeheader()
            writer.writerow({k: row.get(k, "") for k in LOG_FIELDS})
        if header:
            clean_logs()

    try:
        LOG_DIR.mkdir(exist_ok=True)
        write(path)
    except PermissionError:
        alt = LOG_DIR / f"run_{stamp}_alt.csv"
        try:
            write(alt)
        except OSError as exc:
            print("写日志失败：", exc)
            return
        if not _ALT_WARNED:
            _ALT_WARNED = True
            print(f"!! {path.name} 被别的程序占着（多半是 Excel 开着），"
                  f"这段日志改写到 {alt.name}，关掉 Excel 后可以合并")
    except OSError as exc:
        print("写日志失败：", exc)


def save_stuck_shot() -> str:
    """卡住时存一张画面，事后能知道当时屏幕上是什么。

    存图失败不能把报警本身带倒，所以包在 try 里，失败只把原因当路径返回。
    """
    try:
        img = screenshot()
        STUCK_DIR.mkdir(exist_ok=True)
        path = STUCK_DIR / f"stuck_{time.strftime('%Y%m%d_%H%M%S')}.png"
        cv2.imwrite(str(path), img)
        return str(path)
    except Exception as exc:
        return f"存图失败（{exc}）"


def check_stuck(result: str, streak: int) -> int:
    """维护连续 no_world 计数，到点就报警存图。返回新的计数。

    只有 no_world 计数。它意味着 ensure_world 点了公屏入口也没等到锚点，
    多半是游戏掉线或被弹回了登录界面。其余返回码都说明还在正常干活，
    一出现就清零。
    """
    if result != "no_world":
        return 0

    streak += 1
    if streak == NO_WORLD_ALERT or (
            streak > NO_WORLD_ALERT and streak % NO_WORLD_ALERT_EVERY == 0):
        path = save_stuck_shot()
        print(f"!! 连续 {streak} 轮找不到世界频道，游戏可能掉线。画面存到 {path}")
    return streak


# ==================== 流程 ====================


# 点一下公屏入口之后，锚点最久等这么久才认定没点开。
# 面板加载约 1.5 秒，但卡的时候会更慢。等满 4 秒还没看到锚点
# 再重点，就不会把刚弹出来的面板又点关掉。
WORLD_TAP_TIMEOUT = 4.0


def wait_world_state(want_open: bool,
                     timeout: float = WORLD_TAP_TIMEOUT) -> bool:
    """等公屏锚点出现（want_open=True）或消失（False），返回等没等到。

    点完公屏入口不能马上截图判状态：面板开、关都有动画，拿动画中途
    截到的帧判「开没开」，十有八九把刚点开的面板又点关掉——脚本
    「一直在打开、关上」的死循环就是这么来的。所以凡是点过公屏入口
    的地方，下一步判断之前都要先过这个等待。锚点提前到位就提前返回，
    不干等满 timeout。
    """
    deadline = time.time() + timeout
    while True:
        if in_world(screenshot()) == want_open:
            return True
        if time.time() >= deadline:
            return False
        time.sleep(POLL)


def open_world(timeout: float = TIMEOUT_WORLD_OPEN,
               close_overlay: bool = True) -> bool:
    """反复点公屏入口，直到左上角出现世界频道锚点。

    点之前先看一眼：面板已经开着就一下都不点——这个坐标是开关，
    开着的时候点一下就给关上了。点完走 wait_world_state 等锚点，
    锚点一出现立刻返回；等满 WORLD_TAP_TIMEOUT 还没出现，才认定
    这一下没点开，处理遮挡再重点。

    close_overlay=False 时不点遮挡/撒花，只点公屏入口。大重启后
    进游戏走这条路：那时候人在自己家，没有「别人家挡着入口的面板」，
    那些坐标在启动页/公告上点到的东西完全不可控。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if in_world(screenshot()):
            return True
        tap(*TAB_WORLD_PT)
        if wait_world_state(True):
            return True

        if not close_overlay:
            continue

        # 等满 WORLD_TAP_TIMEOUT 锚点还没来，才认定这一下没点开，
        # 先处理遮挡再重点
        try:
            _, flower = find(screenshot(), "btn_flower", FLOWER_ROI)
        except FileNotFoundError:
            flower = None
        if flower:
            if VERBOSE:
                print(f"  公屏点不开，找到撒花按钮 @ {flower}，点掉")
            tap(*flower)
        else:
            if VERBOSE:
                print(f"  公屏点不开，先点掉遮挡 @ {WORLD_CLOSE_PT}")
            tap(*WORLD_CLOSE_PT)
        time.sleep(WORLD_CLOSE_GAP)
    return False


def switch_channel1() -> bool:
    """把世界频道切到 1 号。已经在 1 号就直接返回。

    进来时要求锚点已经在画面上，也就是频道面板是开着的。
    新开游戏后点到的很少正好是 1 号，所以这步基本每次重启都会跑。
    """
    for _ in range(CHANNEL_RETRY):
        if in_world1(screenshot()):
            return True
        for pt in (SWITCH_PT, CHANNEL1_PT, CONFIRM_SWITCH_PT):
            tap(*pt)
            time.sleep(SWITCH_GAP)
        if wait_for("anchor_world1", TIMEOUT_CHANNEL, ANCHOR_ROI,
                    TH_WORLD1)[1] is not None:
            return True
    return False


def open_world_simple(timeout: float = TIMEOUT_WORLD_BOOT) -> bool:
    """刚进游戏时开世界频道的简化版：不看锚点、不点遮挡，
    每隔 WORLD_TAP_GAP 秒重复点公屏入口，点到锚点出现为止。

    跟 open_world 的区别：open_world 点之前先看面板开没开（这个坐标
    是开关，开着时点一下会关掉），还会处理压在入口上的遮挡面板。
    刚进游戏时没有那些复杂状态，人也还在登录页/自己家，画面简单，
    盲点是安全的，而且不怕「入口被公告/活动弹窗挡住」这种情况——
    点几下弹窗自己会关或者入口会露出来。

    只在这两个时机用：人工点开始运行时游戏不在前台、大重启之后。
    平时一轮轮的找世界频道还是走 ensure_world 那套完整逻辑。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        tap(*TAB_WORLD_PT)
        if wait_world_state(True):
            return True
        time.sleep(WORLD_TAP_GAP)
    return False


def game_running() -> bool:
    """游戏进程在不在模拟器里跑着。adb 命令失败当不在，交给调用方重试。"""
    try:
        out = sh("shell", "pidof", GAME_PKG).strip()
    except Exception:
        return False
    return bool(out)


def start_game() -> bool:
    """游戏没在跑就把它拉起来，再点进世界频道 1。

    沿用 restart_game 的启动逻辑：monkey 拉起、点 ENTER_PT 两下、
    等加载页过去。区别是不 force-stop（游戏本来就没在跑），
    开世界频道走 open_world_simple 那套简化逻辑。
    """
    if game_running():
        return True
    print("游戏没在运行，先启动")
    try:
        sh("shell", "monkey", "-p", GAME_PKG,
           "-c", "android.intent.category.LAUNCHER", "1")
    except Exception as exc:
        print("启动命令发不出去，可能是模拟器本身没开好：", exc)
        return False

    time.sleep(BOOT_WAIT)
    tap(*ENTER_PT)
    time.sleep(ENTER_GAP)
    tap(*ENTER_PT)

    import enter_wait
    load_deadline = time.time() + enter_wait.TIMEOUT_TOTAL
    while time.time() < load_deadline:
        if not enter_wait.in_loading(screenshot()):
            break
        if VERBOSE:
            print("  还在加载页，等它过去再点公屏")
        time.sleep(POLL)

    if not open_world_simple():
        print("启动后点不出世界频道")
        return False
    if not switch_channel1():
        print("启动后切不到 1 号频道")
        return False

    print("启动完成，已进入世界频道 1")
    return True


def restart_game() -> bool:
    """关掉游戏重开，再自己点回世界频道 1。返回是否真的回到了 1 号频道。

    force-stop 会把这个包的所有进程一起杀掉，等于手动划掉后台，不需要 root。
    拉起用 monkey 而不写 am start -n 包名/类名，这样游戏更新改了
    启动 Activity 也不用跟着改。

    注意这只能治游戏卡死。如果是模拟器本身卡住、adb 发不出命令，
    这里会直接失败返回，得靠外面重启模拟器实例。
    """
    path = save_stuck_shot()
    print(f"!! 重启游戏。重启前的画面存到 {path}")

    try:
        sh("shell", "am", "force-stop", GAME_PKG)
        time.sleep(KILL_WAIT)
        sh("shell", "monkey", "-p", GAME_PKG,
           "-c", "android.intent.category.LAUNCHER", "1")
    except Exception as exc:
        print("重启命令发不出去，可能是模拟器本身卡住了：", exc)
        return False

    time.sleep(BOOT_WAIT)          # 这段时间看不到锚点是正常的
    tap(*ENTER_PT)                 # 进游戏那两下
    time.sleep(ENTER_GAP)
    tap(*ENTER_PT)

    # 游戏更新、登录加载都很慢，这段时间点什么都是白点，
    # 先把加载页等过去再开始点公屏。enter_wait 在运行时才 import，
    # 免得和 core 循环依赖。加载模板没命中（比如开屏画面不认识）时
    # 这段直接跳过，行为跟以前一样
    import enter_wait
    load_deadline = time.time() + enter_wait.TIMEOUT_TOTAL
    while time.time() < load_deadline:
        if not enter_wait.in_loading(screenshot()):
            break
        if VERBOSE:
            print("  还在加载页，等它过去再点公屏")
        time.sleep(POLL)

    # 刚开游戏人在登录页/自己家，用简化版开公屏：不判锚点、不点遮挡，
    # 一直点公屏入口直到锚点出现。见 open_world_simple 的注释
    if not open_world_simple():
        print("重启后点不出世界频道")
        return False
    if not switch_channel1():
        print("重启后切不到 1 号频道")
        return False

    print("重启完成，已回到世界频道 1")
    return True


def ensure_world() -> bool:
    """确保处于世界频道 1。找钻饭之前必须先过这一关。

    三种情况：已在 1 号就什么都不做；在其他频道就直接切频道；
    两个锚点都不在就先把公屏点开，再切到 1 号。

    拜访结束后的返程也走这里：在别人家园点公屏，打开的就是世界频道。
    """
    img = screenshot()
    if in_world1(img):
        return True

    if not in_world(img) and not open_world():
        return False

    return switch_channel1()


def refresh_world() -> None:
    """关掉公屏再打开，换一批消息。

    同一个坐标点两下：第一下关，第二下开。关键在两下都要等状态坐实：
    第一下之后等锚点真的消失（确认关掉了）才点第二下，不然面板
    关闭动画没走完，第二下点的其实是「正在关的面板」，方向就反了；
    第二下之后等锚点真的回来再返回，不然调用方下一帧判「面板没在」，
    又把刚开的面板点关掉。
    """
    if VERBOSE:
        print(f"  {TIMEOUT_TARGET:.0f} 秒没找到钻饭，关掉公屏再打开")
    tap(*TAB_WORLD_PT)
    wait_world_state(False)      # 确认关掉了再开
    tap(*TAB_WORLD_PT)
    wait_world_state(True)       # 确认开回来了再走


def is_fan(img, hit: tuple[int, int]) -> bool:
    """公屏上这条钻饭消息是不是粉丝发的。

    只在钻饭字样所在的那一行里找粉丝字样，不搜整个聊天面板，
    否则别人那一行的粉丝标记会被算到这一行头上。
    横向范围沿用 CHAT_ROI，纵向取钻饭中心上下各 FANS_BAND 像素。

    条带高度是 2 * FANS_BAND，必须大于粉丝模板的高度，否则 match_one
    会因为区域比模板小而直接返回 0 分，表现成永远不是粉丝。

    还没裁粉丝模板时返回 False，脚本退回原来的流程，不报错。
    """
    h = img.shape[0]
    y1 = max(0.0, (hit[1] - FANS_BAND) / h)
    y2 = min(1.0, (hit[1] + FANS_BAND) / h)
    roi = (CHAT_ROI[0], y1, CHAT_ROI[2], y2)

    try:
        score, pt, name = find_any(img, "fensi", roi)
    except FileNotFoundError as exc:
        if VERBOSE:
            print(f"  {exc}，跳过粉丝判断")
        return False

    if VERBOSE and pt:
        print(f"  同一行有粉丝标记 {name} {score:.3f} @ {pt}")
    return pt is not None


def card_buttons(mode: int):
    """在资料卡上找关注与取关按钮，返回 (关注按钮坐标, 取关按钮坐标)。

    这两个按钮占资料卡上的同一个位置，只是文字从关注变成取关，
    所以正常情况下最多只有一个命中。用两张独立模板而不是算坐标偏移，
    是因为偏移量在已关注和未关注两种卡上未必一致。

    取关按钮只有档 2 才需要知道，其他档跳过那次匹配，省一次模板匹配。

    缺模板时当没找到处理，脚本退回直接拜访，不报错。这一点对档 2 尤其
    重要：btn_unfollow 没裁好的话，档 2 会退化成每家都进门，
    只是不省时间，不会漏吃。

    档 2 还有一个治不了的情况：对方玩家的隐私设置会让关注点不上去，
    按钮永远不会变成取关，这家会被当成没来过，下次还会再进一次。
    """
    img = screenshot()
    follow_pt = None
    unfollow_pt = None

    try:
        _, follow_pt = find(img, "btn_follow")
    except FileNotFoundError as exc:
        if VERBOSE:
            print(f"  {exc}，跳过关注判断")

    if modes.needs_unfollow_check(mode):
        try:
            _, unfollow_pt = find(img, "btn_unfollow")
        except FileNotFoundError as exc:
            if VERBOSE:
                print(f"  {exc}，跳过取关判断")

    return follow_pt, unfollow_pt


def next_stars() -> tuple[tuple[int, int] | None, bool, bool]:
    """抓完一只后找下一个三星。

    返回 (三星坐标, 是否已回到世界频道, 是否被撒花界面挡着)。
    第三个值只在一种情况下为真：点了 FLOWER_MAX 次撒花按钮它还在，
    三星始终没有露出来。这种局面不能当吃到，因为三星可能一直被那层盖着。

    找不到三星不等于抓完了。派对家园会在抓虫弹窗退场后盖上一层撒花界面，
    把三星气泡挡住。所以只要画面上还有撒花按钮就不能退出，要先点掉它，
    再找一遍三星，最多 FLOWER_MAX 轮。三星和撒花按钮都不在了，才算抓完。

    这么做是为了不动 POP_WAIT。撒花界面有加载动画，点三星后 0.1 秒那次
    检测可能撞空，但抓完这一轮它肯定已经出来了。把容错放在这里，
    只有派对家园会多花时间；加长 POP_WAIT 是每一只虫都多花。
    """
    for _ in range(FLOWER_MAX):
        img = screenshot()

        if in_world(img):
            return None, True, False

        _, stars, _ = find_any(img, "three_stars", threshold=TH_STARS)
        if stars:
            return stars, False, False

        _, flower = find(img, "btn_flower")
        if not flower:
            return None, False, False

        tap(*flower)
        if VERBOSE:
            print(f"  没找到三星但撒花按钮还在 @ {flower}，点掉再找")
        time.sleep(FLOWER_DELAY)

    # 撒花按钮点了 FLOWER_MAX 次还在，三星可能一直被挡着
    return None, False, True


def catch_all_bugs(first: tuple[int, int]) -> tuple[int, str]:
    """在别人家园里连抓。返回 (抓了几次, 退出原因)。

    退出原因就是日志里的 exit 列，四种，只有第一种算吃到：
        clean    三星和撒花按钮都不在了。这一家吃到了
        max      抓满 STARS_MAX 次还看得见三星。说明一直没吃上
        back     中途回到了世界频道。无法确认，按没吃到算
        blocked  撒花界面点不掉，三星可能一直被挡着。也按没吃到算

    一家可能挂着多个三星气泡，抓完一个要重新截图再找下一个。
    位置每次都重新匹配，不复用上一次的坐标，因为气泡消失后布局会变。

    三条退出条件：三星和撒花按钮都不在了、画面已经回到世界频道、抓够
    STARS_MAX 次。最后一条是兜底：三星在 0.80 上仍可能被背景撞出固定假峰，
    没有次数上限的话会在同一个点上死循环。

    第一条里「撒花按钮也不在」这半句很关键，具体判断看 next_stars：
    光看不到三星不能退出，因为撒花界面会把气泡挡住。
    """
    stars: tuple[int, int] | None = first
    caught = 0
    why = "clean"

    while stars and caught < STARS_MAX:
        # 三星可能正好压在虚拟摇杆或常驻 UI 控件上，那一下会被它们接走。
        # 改点距原落点最近的合法点，详见 avoid.dodge。
        tap(*avoid.dodge(stars))
        time.sleep(POP_WAIT)          # 等抓虫按钮弹出

        # 点开三星气泡后还可能盖上一层派对撒花界面，抓虫按钮被挡住。
        # 这里只点一次就往下走，不像进门那次要反复确认：
        # 万一没点掉，下一轮循环三星还在，会再走一遍这段。
        _, flower = find(screenshot(), "btn_flower")
        if flower:
            tap(*flower)
            if VERBOSE:
                print(f"  三星后出现撒花按钮 @ {flower}，先撒花")
            time.sleep(FLOWER_DELAY)

        # 除虫按钮只有头 BUG_MAX 个气泡上点才真按。后面的气泡照点
        # （万一是真气泡，点开看一下有没有饭），但不再按除虫：
        # 两次还没吃上，后面再按也是浪费，气泡本身点一下没成本。
        if caught < BUG_MAX:
            tap(*CATCHBUG_PT)
            time.sleep(BUG_WAIT)
        caught += 1

        stars, back, blocked = next_stars()
        if back:                      # 已经不在家园里，不再继续
            why = "back"
            if VERBOSE:
                print("  已回到世界频道，停止抓虫")
            break

        if blocked:                   # 撒花界面点不掉，三星可能还在下面
            why = "blocked"
            if VERBOSE:
                print("  撒花按钮点不掉，本家算没吃到")
            break

        if VERBOSE and stars:
            print(f"  画面上仍有三星 @ {stars}，继续第 {caught + 1} 次")

    # 抓满上限还看得见三星，说明这一家从头到尾没吃上
    if why == "clean" and stars and caught >= STARS_MAX:
        why = "max"

    if VERBOSE:
        print(f"  本家共抓 {caught} 次，退出原因 {why}")
    return caught, why

