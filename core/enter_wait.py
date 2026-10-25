"""点拜访之后的等待：先等加载画面结束，再扫描固定时长找三星。

原先是固定死等一个秒数再开始找，但加载别人家的耗时每次不一样：
等得太短会在加载页上白扫，等得太长每一家都浪费。改成看加载页本身：
加载页消失了才开始计时，家园画面出来多久就扫多久。

加载本身多久都算正常，所以只要看得到加载页就一直等，不因为慢就放弃这一家。
真正要防的是另一种情况：拜访根本没点上、或者对方没给拜访权限，
加载页一眼都没见过。那种情况等下去没任何意义，由 TIMEOUT_NO_LOADING 收场。

进门的判定只有一条：先见到加载页，再看到它消失。不能拿「既不是加载页
也看不到世界频道锚点」当进门：点完拜访有一段过渡帧恰好满足这个条件（资料卡
盖着或公屏面板刚关掉，锚点已经不在，而加载页还没画出来），旧逻辑会在那里
立刻开始扫，日志上表现成「已进门（加载 1.1 秒）」然后扫满 9 秒什么都没看到，
实际上那 9 秒扫的全是加载画面。

加载页的判定方式见 LOADING_ROI 与 loading_score 的注释。
深浅两种底色各一张模板：loading_white、loading_dark，
按 core.variants 的规矩它们是同一个目标的两个变体。

派对家园进门会盖一层撒花界面把三星挡住。碰到就立刻点，不等动画：
点完下一帧截图本身就要花百毫秒级的时间，没点掉就再点，最多 FLOWER_TAPS_MAX 次。
每点一次撒花把扫描时间重新计满，相当于从撒花消失那一刻开始算 SCAN_SECS。

两个入口：
    enter_home()  从公屏点拜访之后用，等加载再扫三星。调用方在 zuanfan_bot.one_round
    scan_home()   已经确认进门了，只扫三星。第四档的 follow_list 走自己的等加载
                  流程，所以只用这一段
core 里原来那份同名函数已删除。
"""

from __future__ import annotations

import time

import cv2
import numpy as np

import core

# 加载页不走 core 的彩色或灰度匹配，它有自己的一套，见 loading_score。
# 所以这里不再往 core.COLOR_TPLS 里注册 loading。

LOADING = "loading"

# 加载页上那条固定不变的图案所在的区域，原图像素 (419, 336) 到 (537, 366)。
# 这里放到 (405, 320) 到 (551, 382)，四周各留十几到二十像素防漂移。
# 区域宽 146、高 62，都大于模板的 118x28，不会因为区域比模板小而返回 0 分。
#
# 一开始用的是整张 960x540 的页面当模板，靠满屏底色判定，深色页永远认不出来。
# 原因是加载页中间的插图每次都不一样，而 TM_CCOEFF_NORMED 会先减掉均值，
# 四周那片纯色底几乎不贡献分数，整张图的分数基本全压在会变的插图上。
LOADING_ROI = (0.4219, 0.5926, 0.5740, 0.7074)

# 局部二值化的两个参数。比周围 BLUR 那一块的均值亮过 BIAS 就算字。
# BLUR 要明显大于笔画宽度，小了字会变空心；BIAS 抬高一点能滤掉底色噪点。
BLUR = (15, 15)
BIAS = 6

# 加载页的阈值。二值化之后只比字形，分数比以前稳得多，沿用标准线。
TH_LOADING = 0.85

SCAN_SECS = 5.0          # 加载页消失后找三星的时长。每点一次撒花就重新计满

# 撒花最多点几次。不用 core.FLOWER_MAX（那个是点三星之后那套逻辑的上限）：
# 这里不等动画，一帧一次，次数给得起。点满这么多次还在就当它点不掉了。
FLOWER_TAPS_MAX = 10

# 一眼都没见过加载页的上限。两种情况走到这里：拜访根本没点上，
# 或者对方没给拜访权限。两种都是等再久也不会变，直接收场跑下一轮。
# 取 9 秒：比真实加载延迟宽裕，又不至于每碰一个没权限的就空耗十几秒。
# 只要见过一帧加载页，这个上限就不再生效，加载多久都等。
TIMEOUT_NO_LOADING = 9.0

# 整个 enter_home 的硬上限。不是用来限加载时间的，而是最后一道保险：
# 加载页卡死不动、撒花反复重置计时这类情况下，不能让一轮永远不返回，
# 否则上层的卡死检测和重启游戏那套机制也跑不到。正常加载远达不到这个值。
TIMEOUT_TOTAL = 120.0

_tpl_cache: dict[str, np.ndarray] = {}


def binarize(gray: np.ndarray) -> np.ndarray:
    """局部二值化：比周围亮的算字，其余算底。

    不用 core 那个固定阈值的二值化（BINARY_TH），因为深浅两种加载页的
    绝对亮度差了一百多级，一个固定阈值没法同时伺候两边。
    比周围亮这个判法只看局部对比，不看绝对亮度，两种底色都能拿到同一块字形。
    """
    return ((gray > cv2.blur(gray, BLUR) + BIAS).astype(np.uint8)) * 255


def loading_tpl(name: str) -> np.ndarray:
    """读一张加载页模板并二值化。自己缓存，不走 core._cache。"""
    if name not in _tpl_cache:
        path = core.TPL_DIR / f"{name}.png"
        img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if img is None:
            raise FileNotFoundError(f"缺少模板图 {path}")
        _tpl_cache[name] = binarize(img)
    return _tpl_cache[name]


def loading_score(img):
    """在 LOADING_ROI 里找加载页。返回 (最高分, 中心点, 模板名)。

    不直接用 core.find_any，因为这里要先把截图和模板都二值化。

    为什么必须二值化：模板 118x28 里字只占 20.6%，底色占 79.4%，
    TM_CCOEFF_NORMED 的分数基本是底色在决定。浅色加载页的底接近纯白、
    几乎不变，所以一直稳；深色加载页的底是插图的一部分，每次加载的图不同，
    底色一变分数就掉。拿 loading_dark 做扰动实验的数据：
        底色左右渐变 ±20 → 0.929，±40 → 0.769，±60 → 0.609
        底色换成另一种颜色 → 0.58 到 0.95
    同一组扰动先二值化再匹配：
        底色左右渐变 ±60 → 0.996，底色整体抬到 90 → 0.866

    两张模板不能合并成一张：二值化之后 dark 与 white 互相只有 0.481，
    因为两张裁的位置差了 2 像素，而且 white 左下角裁进了别的 UI 块。
    """
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    h, w = gray.shape[:2]
    x1, y1, x2, y2 = LOADING_ROI
    ox, oy = int(x1 * w), int(y1 * h)
    region = binarize(gray[oy:int(y2 * h), ox:int(x2 * w)])

    best = (0.0, None, "")
    for name in core.variants(LOADING):
        tpl = loading_tpl(name)
        if region.shape[0] < tpl.shape[0] or region.shape[1] < tpl.shape[1]:
            continue
        res = cv2.matchTemplate(region, tpl, cv2.TM_CCOEFF_NORMED)
        _, score, _, loc = cv2.minMaxLoc(res)
        th, tw = tpl.shape[:2]
        pt = (ox + loc[0] + tw // 2, oy + loc[1] + th // 2)
        if core.DEBUG:
            print(f"  find {name:16s} {score:.3f} @ {pt}")
        if score > best[0]:
            best = (score, pt, name)
    return best


def in_loading(img) -> bool:
    """这一帧是不是加载页。缺模板时当作不是，不报错。"""
    try:
        score, _, _ = loading_score(img)
    except FileNotFoundError as exc:
        if core.VERBOSE:
            print(f"  {exc}，跳过加载页判断")
        return False
    return score >= TH_LOADING


def scan_home(pending=None, hard=None):
    """已经进门了，扫 SCAN_SECS 找三星。返回三星坐标或 None。

    每帧同时看撒花按钮和三星：
        有撒花就立刻点（不等动画），点完下一帧再看，没消失就再点；
        有三星就返回坐标，交给 catch_all_bugs 去点。
        撒花点过之后扫描时间重新计满 SCAN_SECS。

    中途又看到世界频道锚点说明被弹回公屏了，直接放弃这一家。

    pending 是调用方手上已经有的那一帧，传进来可以少截一次图。
    hard 是绝对时间戳形式的总上限，不给就从现在起算 TIMEOUT_TOTAL。
    """
    if hard is None:
        hard = time.time() + TIMEOUT_TOTAL

    flower_taps = 0
    scan_end = time.time() + SCAN_SECS

    while time.time() < scan_end and time.time() < hard:
        img = pending if pending is not None else core.screenshot()
        pending = None

        if core.in_world(img):
            if core.VERBOSE:
                print("  被弹回了公屏，放弃这一家")
            return None

        # 撒花界面盖在三星上面，先点它。不等动画：下一帧截图自带延迟，
        # 没点掉下一圈再点，直到它消失或点满 FLOWER_TAPS_MAX 次。
        if flower_taps < FLOWER_TAPS_MAX:
            _, flower = core.find(img, "btn_flower")
            if flower:
                core.tap(*flower)
                flower_taps += 1
                if core.VERBOSE:
                    print(f"  撒花第 {flower_taps} 次 @ {flower}，扫描重新计时")
                scan_end = time.time() + SCAN_SECS
                continue

        _, stars, _ = core.find_any(img, "three_stars",
                                    threshold=core.TH_STARS)
        if stars:
            if core.VERBOSE:
                print(f"  找到三星 @ {stars}")
            return stars

        # 不再额外睡：adb 截图本身就要零点几秒，一帧接一帧扫最快

    if core.VERBOSE:
        print(f"  扫完 {SCAN_SECS:.0f} 秒没看到三星")
    return None


def enter_home():
    """等加载结束再找三星。返回三星坐标，没进去或没找到都返回 None。

    阶段一在这里，只认一条：先见到加载页，再看到它消失，才算进门。
    没见过加载页之前，不管当前这一帧是公屏、资料卡还是什么都不算数，继续等，
    等满 TIMEOUT_NO_LOADING 就当拜访没点上或对方没给权限，直接放弃这一家。
    返回之后上层会自己跑 ensure_world 回公屏，这里不用管。

    见过加载页之后就不再看这个上限了，加载多久都等，只剩 TIMEOUT_TOTAL 兵底。
    加载完可能先闪一帧公屏，所以那之后看到锚点也接着等。

    阶段二交给 scan_home。
    """
    t0 = time.time()
    hard = t0 + TIMEOUT_TOTAL
    loading_seen = False
    noted = False
    pending = None

    while True:
        if time.time() > hard:
            if core.VERBOSE:
                print(f"  卡在进门这一步超过 {TIMEOUT_TOTAL:.0f} 秒，放弃这一家")
            return None

        img = core.screenshot()

        if in_loading(img):
            if core.VERBOSE and not noted:
                print("  加载页，等它过去")
                noted = True
            loading_seen = True
            time.sleep(core.POLL)
            continue

        # 还没见过加载页：这一帧是什么都不算进门。点完拜访有一段过渡帧
        # 既不是加载页也看不到锚点，拿它当进门会把整段扫描时间耗在加载画面上。
        if not loading_seen:
            if time.time() - t0 > TIMEOUT_NO_LOADING:
                if core.VERBOSE:
                    print(f"  {TIMEOUT_NO_LOADING:.0f} 秒没出加载页，"
                          f"拜访没点上或没拜访权限，放弃这一家")
                return None
            time.sleep(core.POLL)
            continue

        # 见过加载页了。锚点还在说明刚好闪回了公屏那一帧，接着等。
        if core.in_world(img):
            time.sleep(core.POLL)
            continue

        pending = img   # 这一帧已经是家园画面了，留给阶段二用，不重复截一次
        break

    if core.VERBOSE:
        waited = time.time() - t0
        print(f"  已进门（加载 {waited:.1f} 秒），扫 {SCAN_SECS:.0f} 秒找三星")

    return scan_home(pending, hard)
