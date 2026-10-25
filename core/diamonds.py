"""晶钻计数：读屏幕上方的晶钻数，每吃到一家就记一笔。

每成功吃到一口钻饭，晶钻数就加 1。脚本以为吃到了、实际没吃到时
这个数不动。拿两边的差对一下就能看出脚本的准确率。

这个数只用来写日志和判当天的 100 口上限，不用来判这一家要不要接着吃。
OCR 自己也会读错，拿它当实时判据会把单家的流程带偏。

同一个游戏日内重开脚本时，起点从当天的晶钻日志里恢复（见
recover_today），不从零重新算，否则 100 颗的硬上限等于没设。
日志文件名按游戏日命名（core.game_day），凌晨 5 点前算前一天，
并且带账号名（user.py），两个号同一天跑各有各的日志。
要不要恢复、开跑前手工吃过几口，也在 user.py 里按账号设置。

OCR 要装两个东西：pytesseract 是壳，Tesseract 是真正识字的程序。
两者少一个就读不出数，此时这个模块全部返回 None，不报错，
不影响吃饭本身，只是日志里的晶钻列是空的。
坐标全部在 960x540 下标定，分辨率变了要重测。

晶钻数只在主界面和家园（自己家、别人家）里显示，关注列表和
世界频道的面板一开就把它挡住了。所以读数时机有硬约束：
开工读数必须抢在打开关注列表之前，吃到后的读数必须抢在
回世界频道之前、趁人还在别人家里。

开工读数失败时不应该就这么不计数地跑：100 颗上限是唯一的硬保护，
悄悄关掉太容易吃超。所以读不到就问人（ASK 钩子）：面板把它指到
弹窗，命令行指到 console_ask。人也不知道（取消/回车）才不计数。
"""

from __future__ import annotations

import csv
import os
import re
import time
from pathlib import Path

import core
import user

# 晶钻图标只会出在屏幕上方这一条里。原图像素 (279, 6) 到 (552, 39)。
TPL = "diamonds"
ICON_ROI = (0.2906, 0.0111, 0.5750, 0.0722)

# 数字在图标右边。以图标中心为这个横过来的矩形左边那条宽边的中点，
# 所以横向从中心往右取 NUM_W 像素，纵向上下各取一半。
# 左边缘正好压在图标中心上：图标约 15 像素宽，右半边的 7、8 个像素
# 会混进 OCR 区域的最左端，OCR 偶尔把它认成数字（错位就是这样来的）。
# 裁掉左边 NUM_SKIP（总宽的四分之一）再送 OCR，图标被干净切掉，
# 离数字区还有十几像素余量；图标跟着数字位数平移时也不受影响——
# 它平移的方向是往左，不会往右顶进数字区。
NUM_W = 84
NUM_H = 20
NUM_SKIP = NUM_W // 4   # 从左边裁掉的像素数，只留右边 3/4

# 整数，一般不超过八位。超过这个位数当 OCR 读错，丢掉不用。
MAX_DIGITS = 8

# 一次读数跟上一次相比，超出 0 到 JUMP_MAX 这个范围就当 OCR 读错，
# 丢弃这次读数。正常吃一口只加 1，漏记一两次也就多 2、3 颗；
# 一下多出 20 颗以上只可能是位数读错了。往下掉同理：晶钻数一天里
# 只涨不跌，6011 读成 560 这种漏位错读也要拦。不拦的话，一个错读
# 就能直接顶穿 DAILY_LIMIT，或者把 last 拖到一个离谱的低位，
# 后面每次恢复都跟着错。
JUMP_MAX = 20

# 游戏一天最多吃 100 口钻饭，这是游戏定的硬上限，不会变。
GAME_DAILY_MAX = 100

# 今天的目标颗数：面板上「满 __ 颗时问我」填的那个数，默认等于硬上限。
# 用户填 69 的意思是「今天已经吃了 31，再吃 69 就停」。
# 晶钻比开工时多出这么多颗就是吃满了。
DAILY_LIMIT = 100

# 晶钻日志单独一个文件，不跟 run_*.csv 混。每吃到一家写一行。
#     time      时间
#     source    这一口是在哪里吃的，list 是关注列表，world 是世界频道，
#               manual 是面板上暂停时手工改起点记的一笔（diamonds 列是
#               改完的新起点，gained/delta/ok 留空——恢复时这两列留空的行
#               不参与起点反推，也不算最近读数，所以这笔不会带偏恢复）
#     eaten     脚本自己数的口数
#     diamonds  当时读到的晶钻数
#     gained    比开工时多了多少
#     delta     比上一口多了多少，正常是 1
#     ok        delta 至少为 1 时是 1，否则是 0。这一列算准确率
LOG_DIR = Path("logs")
LOG_FIELDS = ("time", "source", "eaten", "diamonds", "gained", "delta", "ok")

# 开工读数失败时「问人」的钩子：接受一条提示语，返回一个非负整数，
# 或者返回 None 表示人也不知道/不想给（这一段就不计数）。
# panel.py 打开面板时会把它指到弹窗版；命令行入口指到 console_ask。
# 不指的话读不到就静默不计数，跟最早的行为一样。
ASK = None


def console_ask(prompt: str) -> int | None:
    """问人钩子的命令行版：在控制台里输入，回车跳过。"""
    try:
        raw = input(prompt).strip()
    except EOFError:
        return None
    return int(raw) if raw.isdigit() else None


# 装完 Tesseract 后 PATH 没生效是常事，所以把常见安装位置逐个试一遍。
# 装到了其他目录的话，把那个路径加到这个列表里。
TESSERACT_DIRS = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"),
    os.path.expandvars(r"%LOCALAPPDATA%\Tesseract-OCR\tesseract.exe"),
    os.path.expandvars(r"%USERPROFILE%\AppData\Local\Tesseract-OCR\tesseract.exe"),
)

# 找到过一次就记住。每次 OCR 都会调 setup_tesseract，不记住的话
# 每吃到一口控制台就重复报一遍路径，全是噪音
_TESS_OK = False

OCR_CONFIG = "--psm 7 -c tessedit_char_whitelist=0123456789"


def setup_tesseract(pytesseract) -> bool:
    """把 pytesseract 指向真正的 tesseract.exe。找到了返回 True。

    PATH 里已经有就不动。PATH 里没有就把 TESSERACT_DIRS 里的路径逐个试，
    哪个文件存在就用哪个。读关注人数和读晶钻数用的是同一份。
    找到过一次就存在 _TESS_OK 里，之后直接返回，不再重复找、重复说。
    """
    import shutil

    global _TESS_OK
    if _TESS_OK:
        return True
    if shutil.which("tesseract"):
        _TESS_OK = True
        return True
    for path in TESSERACT_DIRS:
        if os.path.exists(path):
            pytesseract.pytesseract.tesseract_cmd = path
            _TESS_OK = True
            # 找到了不用说：这是脚本自己的内部细节，跟用户没关系。
            # 诊断时加 --debug 才打
            if core.DEBUG:
                print(f"  用写死的 Tesseract 路径：{path}")
            return True
    return False


def ocr_int(crop):
    """在一小块图上读一个整数。读不出来返回 None。"""
    try:
        import pytesseract
    except ImportError:
        print("  没装 pytesseract，读不了晶钻数")
        return None

    if not setup_tesseract(pytesseract):
        print("  找不到 tesseract.exe，读不了晶钻数。试过的路径：")
        for path in TESSERACT_DIRS:
            print(f"    {path}")
        return None

    try:
        text = pytesseract.image_to_string(crop, config=OCR_CONFIG)
    except Exception as exc:
        print(f"  晶钻 OCR 失败：{exc}")
        return None

    m = re.search(r"\d+", text)
    if not m:
        print(f"  晶钻 OCR 没读到数字，原文：{text!r}")
        return None
    if len(m.group(0)) > MAX_DIGITS:
        print(f"  晶钻 OCR 读出 {len(m.group(0))} 位数，超过 {MAX_DIGITS} 位，"
              f"当读错丢掉（原文 {text.strip()!r}）")
        return None
    return int(m.group(0))


def crop_number(img):
    """在 img 上找晶钻图标并裁出数字区。

    返回 (score, 图标中心, 数字区小图)。没找到图标时后两个是 None。
    缺模板时抛 FileNotFoundError，由调用方决定怎么处理。

    不写死数字区域的绝对坐标：晶钻数位数一变，整排图标的位置就会平移。
    以图标中心为基准，横向从中心 + NUM_SKIP 处开始取到中心 + NUM_W，
    纵向上下各取一半。NUM_SKIP 的作用是把混进区域左端的半个图标
    裁掉，理由见 NUM_W 上方的注释。
    """
    score, pt = core.find(img, TPL, ICON_ROI)
    if not pt:
        return score, None, None

    h, w = img.shape[:2]
    cx, cy = pt
    x0 = min(w, cx + NUM_SKIP)
    x1 = min(w, cx + NUM_W)
    y0 = max(0, cy - NUM_H // 2)
    y1 = min(h, y0 + NUM_H)
    crop = img[y0:y1, x0:x1]
    if crop.size == 0:
        return score, pt, None
    return score, pt, crop


def read(img=None):
    """读屏幕上方的晶钻数。读不出来返回 None。

    先在 ICON_ROI 里找晶钻图标，再以它中心为基准裁一块
    (NUM_W - NUM_SKIP) x NUM_H 去 OCR。缺模板时不报错，返回 None。
    """
    if img is None:
        img = core.screenshot()

    try:
        score, pt, crop = crop_number(img)
    except FileNotFoundError as exc:
        if core.VERBOSE:
            print(f"  {exc}，读不了晶钻数")
        return None

    if not pt:
        if core.VERBOSE:
            print(f"  没找到晶钻图标（最高分 {score:.3f}），读不了晶钻数")
        return None
    if crop is None:
        return None
    return ocr_int(crop)


def recover_today() -> tuple[int, int] | None:
    """从今天（游戏日）的晶钻日志里把起点和最近一次读数挖回来。

    返回 (start, last)。没有日志、一行有效读数都没有、或文件读不动时
    返回 None，调用方退回现场读屏。

    起点的推法：第一行带 gained 的记录，start = diamonds - gained。
    last 的推法：最后一行「验证过」的读数，也就是 delta 在 0 到
    JUMP_MAX 之间的行。delta 留空的行（暴涨/暴跌被丢弃的错读、
    读不出来的行）一律跳过——旧日志里 56009、560 这种垃圾值
    就是这么进来的，不能拿它们当最近读数。

    为什么要恢复：同一天内把脚本关了重开，起点要是从重开那刻重新算，
    100 颗的硬上限就等于没设，很容易一不小心吃超、白跑一整圈。
    在 user.py 里把「重新识别初始晶钻数」设为是的账号不走这里，
    开跑时现场读屏重记起点。
    """
    path = log_path()
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
    except OSError:
        return None

    start = None
    last = None
    for row in rows:
        try:
            n = int(row.get("diamonds") or "")
        except ValueError:
            continue
        if start is None:
            try:
                start = n - int(row.get("gained") or "")
            except ValueError:
                pass
        try:
            delta = int(row.get("delta") or "")
        except ValueError:
            continue                # 被丢弃的错读/没读出来的行，不可信
        if 0 <= delta <= JUMP_MAX:
            last = n

    if start is None or last is None:
        return None
    return start, last


def log_path() -> Path:
    """今天（游戏日）当前账号的晶钻日志路径。账号见 user.py。"""
    return LOG_DIR / f"diamonds_{core.game_day()}_{user.current()['name']}.csv"


def baseline_path() -> Path:
    """今天（游戏日）当前账号的晶钻基线文件路径。

    文件里就一个整数：当天第一次现场读屏读到的原始数，不扣手工口数。
    手工吃几口是开跑时的设置，每次都从原始基线往下扣，这样重开脚本、
    中途改了手工口数，也不会重复扣或者漏扣。
    """
    return LOG_DIR / f"diamonds_start_{core.game_day()}_{user.current()['name']}.txt"


def read_baseline() -> int | None:
    """读今天的基线。没有或读不动返回 None。"""
    try:
        return int(baseline_path().read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def write_baseline(raw: int) -> None:
    """写下今天的基线。写不进去不能打断跑圈，只是少了一层恢复手段。"""
    try:
        LOG_DIR.mkdir(exist_ok=True)
        baseline_path().write_text(str(raw), encoding="utf-8")
    except OSError as exc:
        print(f"  写晶钻基线失败：{exc}，今天重开脚本时起点只能靠日志反推")


def log_row(row: dict) -> None:
    """往当天的晶钻日志追加一行。写盘失败不能打断跑圈，所以包在 try 里。

    编码用 utf-8-sig，Excel 双击打开不会乱码。
    """
    path = log_path()
    try:
        LOG_DIR.mkdir(exist_ok=True)
        header = not path.exists()
        with path.open("a", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=LOG_FIELDS,
                                    extrasaction="ignore")
            if header:
                writer.writeheader()
            writer.writerow({k: row.get(k, "") for k in LOG_FIELDS})
    except OSError as exc:
        print("写晶钻日志失败：", exc)


class Tracker:
    """记住开工时的晶钻数，每吃到一家再读一次。

    start 是开工时读到的数，last 是最近一次读到的数。
    两个都可能是 None：没装 OCR、没裁模板、或者那一帧没读出来。
    读不出数时这个类所有判断都返回“没到上限”，不会把吃饭停下来。

    start 也可以从外面传进来。先跑关注列表再转世界频道时，两段用同一个
    起点，不能到世界频道重新读一个，否则列表里吃掉的那些口就不算了。

    enabled 为 False 时这个计数器整个是摆设：begin/after_meal 什么都不做，
    full 永远 False。面板上把「晶钻计数」勾掉时就用它，调用方不用
    到处判断计数到底开没开。
    """

    def __init__(self, source: str, start: int | None = None,
                 limit: int | None = None, enabled: bool = True) -> None:
        self.source = source
        # limit 不能写成 DAILY_LIMIT 当默认值：Python 的默认参数在
        # 模块加载那一刻就定死了，面板上改过的颗数传不进来。
        # 创建时才去读，面板先设好再开跑，读到的就是用户填的数。
        self.limit = limit if limit is not None else DAILY_LIMIT
        self.start = start
        self.last = start
        self.enabled = enabled

    def begin(self) -> int | None:
        """开工时定起点。顺序：外面传进来的 > 基线文件/今天的日志 > 现场读屏。

        基线文件记的是当天第一次现场读屏的原始数（见 baseline_path）。
        手工吃过的口数（user.py 的设置）每次都从原始基线上往下扣，
        所以无论走哪条恢复路径，满 100 的判断都包含手工吃的那几口；
        重开脚本时也不会重复扣。

        「重新识别初始晶钻数」是真开关：是 = 无视基线和日志，现场读屏
        重设起点；否 = 有记录就恢复。它的默认是否（user.py），所以
        「重开同号忘了改设置就把 100 上限重置」这条危险路径走不到。
        """
        if self.start is not None:
            core.print_blue(f"开工时的晶钻数：{self.start}（上一段传过来的）")
            return self.start

        if not self.enabled:
            return None

        st = user.current()
        raw = None        # 当天第一次现场读屏的原始数，不扣手工口数
        last = None       # 日志里最近一次读到的数
        if st["fresh"]:
            if baseline_path().exists() or log_path().exists():
                core.print_blue("按这个号的设置，无视今天的基线和日志，"
                                "现场重新识别起点")
        else:
            raw = read_baseline()
            rec = recover_today()
            if rec is not None:
                if raw is None:
                    # 老日志反推的起点。基线文件还没有的时候只能靠它，
                    # 反推完顺手把基线文件补上，下回就不用推了
                    raw = rec[0]
                    write_baseline(raw)
                last = rec[1]

        if raw is None:
            got = read()
            if got is None and ASK is not None:
                # OCR 这条路断了就问人。人要一个数比静默不计数强：
                # 100 颗上限是唯一的硬保护，悄悄关掉太容易吃超
                got = ASK("没识别到开工时的晶钻数。看一眼游戏画面，"
                          "输入当前晶钻数目（直接回车 = 这次不计数）：")
                if got is not None:
                    core.print_blue(f"人工报数：开工时的晶钻数按 {got} 算")
            if got is None:
                core.print_blue("没读到开工时的晶钻数，晶钻计数这一块不生效")
                self.start = None
                self.last = None
                return None
            raw = got
            write_baseline(raw)

        self.start = max(0, raw - st["manual"])
        # last 是「画面上的真实读数」，用来跟下一口比 delta，所以必须
        # 拿没扣过手工口数的 raw，不能拿扣过的 start。否则手工吃了 27 口
        # 的话，第一口真读数跟 start 一比就是 +28，会被 JUMP_MAX 熔断
        # 误判成 OCR 读错。
        self.last = last if last is not None else raw

        if st["manual"]:
            core.print_blue(
                f"开工基线 {raw}，扣掉开跑前手工吃的 {st['manual']} 口，"
                f"起点按 {self.start} 算，满 {self.limit} 颗就收工")
        else:
            core.print_blue(f"开工时的晶钻数：{self.start}，"
                            f"满 {self.limit} 颗就收工")
        if last is not None and last != raw:
            core.print_blue(f"今天日志里最近一次读到 {last}，"
                            f"已吃 +{last - self.start}")
        return self.start

    def after_meal(self, eaten: int) -> int | None:
        """以为吃到一家之后读一次，写一行日志。返回这次读到的数。

        delta 是跟上一口的差。真吃到了就是 1，白跑一家就是 0。
        这一列相加除以行数就是脚本判“吃到了”的准确率。

        delta 必须在 0 到 JUMP_MAX 之间才算正常读数：涨太多是位数
        读错，跌了就一定是读错（晶钻一天里只涨不跌）。读错的行
        日志照写（diamonds 列留着实录，delta/ok 留空），但不更新
        计数，更不会拿它去判 DAILY_LIMIT。
        """
        if not self.enabled:
            return None

        now = read()
        row = {"time": time.strftime("%H:%M:%S"), "source": self.source,
               "eaten": eaten, "diamonds": "" if now is None else now}

        delta = None
        if now is not None and self.last is not None:
            delta = now - self.last
        discard = delta is not None and not (0 <= delta <= JUMP_MAX)
        if discard:
            core.print_blue(
                f"  晶钻从 {self.last} 变成 {now}（{delta:+d}），"
                f"超出 0 到 {JUMP_MAX} 的正常范围，当 OCR 读错丢弃，"
                f"计数不动")
        else:
            if now is not None and self.start is not None:
                row["gained"] = now - self.start
            if delta is not None:
                row["delta"] = delta
                row["ok"] = 1 if delta >= 1 else 0
                if core.VERBOSE:
                    mark = "对得上" if delta >= 1 else "晶钻没增加，实际没吃到"
                    core.print_blue(
                        f"  晶钻 {now}（比开工时 +{row.get('gained', '?')}，"
                        f"比上一口 {delta:+d}，{mark}）")

        if not discard and now is not None:
            self.last = now
            if self.start is None:      # 开工那下没读到，拿这一次当起点
                self.start = now

        log_row(row)
        return now

    def set_start(self, value: int) -> None:
        """手工改起点（面板上暂停时用的）。改完写一行日志、更新基线文件。

        只动 start 不动 last：last 是画面上的真实读数，下一口的 delta
        还要跟它比；start 只是算账用的起点，改掉它 gained 跟着变。

        基线文件必须跟着改：同一天里重开脚本时 begin() 优先从基线恢复，
        不改的话这次修正就丢了。基线存的是不扣手工口数的原始数
        （见 baseline_path），所以写的时候把 manual 加回去，恢复时
        再扣一遍正好回到 value。

        线程安全靠调用时机保证：面板只在工作线程真的停稳（parked）时
        才放行这个调用，那时工作线程在空转，不碰这个对象。所以这里
        不加锁——加锁就得让 after_meal 的 OCR 和写盘也捏着锁，反而
        可能卡住界面。
        """
        old = self.start
        self.start = value
        write_baseline(value + user.current()["manual"])
        log_row({"time": time.strftime("%H:%M:%S"), "source": "manual",
                 "diamonds": value})
        core.print_blue(f"手工把晶钻起点从 {old} 改成 {value}，"
                        f"已写进晶钻日志和基线文件")

    def gained(self) -> int | None:
        """比开工时多出多少颗。两端任一个没读到就返回 None。"""
        if self.start is None or self.last is None:
            return None
        return self.last - self.start

    def full(self) -> bool:
        """晶钻数比开工时多了 limit 颗没有。读不出数时返回 False。

        这是当天的硬上限：游戏一天只能吃 100 口，吃满了再跑都是白跑。
        """
        if not self.enabled:
            return False
        g = self.gained()
        return g is not None and g >= self.limit
