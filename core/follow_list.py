"""第四档「关注列表巡回」：按关注列表一个一个拜访，没饭的就取关。

列表跑完后看吃到了多少口：够 EAT_TARGET 口就收工，不够就转世界频道
接着吃（无界面，直接在这条命令行里跑），直到凑满 EAT_TARGET 口。
想带界面操作就走 python core/panel.py，面板是个启动器，选好配置点开始。

另一道上限是晶钻数：每吃到一口晶钻加 1，比开工时多出 100 颗就是
当天吃满了，点右下角两下回自己家再收工（面板上会先问要不要继续刷）。
晶钻计数在 diamonds.py。

坐标全部在 960x540 下标定，分辨率变了要重测。
坐标能写死的前提有两条：每次重新打开关注页时列表都回到顶部，
以及排序是料理排序。两条里少一条，坐标 A 和 B 点到的就不是同一个人。

回自己家有两个办法，不能混用：
    平时用 RESTART_PTS 那右下角两下。
    对方在开派对时用左上角的 HOME_PT：右下角那两下在派对里点不动。
    在不在派对靠 where_after_meal 认「下一家」按钮（xiayijia），
    吃完就看；认漏了还有「坐标 C 点不动」那条老路兜底。

三个锚点各管一事，不要混用：
    好友页标题（anchor_friends）在左上角固定位置，用来判 P1 P2 两下点成没成。
    小耶的名片（anchor_follow）只在关注列表里出现，用来判「在不在关注页」。
    排序标签（wrongorder / correctorder）只用来判排序。
"""

from __future__ import annotations

import argparse
import re
import time
from pathlib import Path

import cv2
import numpy as np

import core
import diamonds
import enter_wait
import user

# 一天总共要吃多少口。关注列表和世界频道吃的合在一起算。
# 计的是吃到的家数，不是拜访的家数。进了门没吃到不算。
# 默认给一个实际碰不到的大数，等于不限，真正的收工条件是晶钻计数。
# 面板上不显示这一项，命令行还能用 --target 改
EAT_TARGET = 999

# 每处理完这么多个人（吃到 + 取关合计），必须回一次自己家再继续：
# 有饭的吃完直接点右下角两下回家，不点坐标 C 跳下一家；
# 没饭的先打开关注列表取关、收掉好友页，再点右下角两下回家。
# 到家后重新打开关注列表点拜访，后面跟正常走向一致
HOME_EVERY = 10

# 打开关注列表要依次点的三下。P1 P2 开好友页，P3 切到关注列表。
OPEN_PTS = ((917, 43), (787, 132), (79, 221))
OPEN_GAP = 1.0
RETRY_PT = OPEN_PTS[2]
RETRY_MAX = 5
RETRY_GAP = 1.0

# 依次点这两下从别人家回自己家。平时回家一律用这两下。
# 人本来就在自己家时不能点，点了会进到别的页面卡住。
RESTART_PTS = ((932, 504), (812, 504))
RESTART_GAP = 1.0
RESTART_MAX = 5
RESTART_TRIES = 3
RESTART_LOADING_WAIT = 5.0

# 左上角的返回按钮。调用时机只有两种，都经 leave_party 走：
# 饭后认出了派对（where_after_meal 看到「下一家」按钮，此时右下角
# 回家那两下和坐标 C 都点不动），或饭后「下一家」和晶钻图标都
# 看不到、画面状态不认识时兜底。其他时候不要用这一下，它不一定能回家。
HOME_PT = (37, 39)
HOME_GAP = 1.0

# anchor_friends 是好友页左上角的标题。模板选区是原图 (16, 15) 到 (115, 45)。
FRIENDS_ANCHOR = "anchor_friends"
FRIENDS_ROI = (0.0083, 0.0130, 0.1302, 0.1019)

# 好友页标题的中心（上面那个选区的中心），兼作返回键：收掉好友页
# 就点这里。模板在时点实测中心，缺模板时点这个写死的位置兜底
FRIENDS_BACK_PT = (66, 30)

# anchor_follow 是小耶的名片。她是 NPC，不拜访也不取关。
ANCHOR = "anchor_follow"
# 平时找小耶最多滑这么多次。刚清完列表会放宽到 PURGE_SWIPE_MAX：
# 清掉一批人之后小耶的位置可能沉到很靠下，25 次不够。
ANCHOR_SWIPE_MAX = 25
PURGE_SWIPE_MAX = 100

# 排序标签只在固定位置的那一小块。原图像素 (150, 78) 到 (298, 142)。
ORDER_ROI = (0.1563, 0.1444, 0.3104, 0.2630)
SORT_PTS = ((223, 110), (222, 236))
SORT_GAP = 1.0
SORT_RETRY = 3
SORT_MARGIN = 0.02

# 关注数在屏幕右上角，内容形如「关注数：x/400」。选区 (803, 15) 到 (941, 34)。
TOTAL_ROI = (0.8365, 0.0278, 0.9802, 0.0630)

# 每天开工前的清列表。把列表里没饭标识的人全取关掉。
#
# 一天只能跑一次，靠 PURGE_DIR 里的日期标记文件记住。日期用本机本地时间，
# 零点过后算新的一天。跑第二次会误删人：当天已经吃过的那些人饭标识也没了，
# 看起来跟本来就没饭的一模一样。
#
# 没饭的判法：找到拜访标识 mark_visit 后，在它上方一个小窗口里找饭标识
# mark_food 的中点。窗口是纵向 FOOD_DY[0] 到 FOOD_DY[1]、
# 横向 FOOD_DX[0] 到 FOOD_DX[1]。窗口里没有饭标识的中点，就是没饭。
#
# FOOD_DY 两个数都是负的：饭标识在拜访标识的上方，不是下方。
# 在一张六人一屏的截图上实测过：四个有饭的人，dy 是 −38 到 −39，
# dx 是 −5 到 −6。纵向按实测值前后各留约 8 像素，横向左右各留 14 像素。
#
# PURGE_ROI 是只在这块里找拜访标识。原图像素 (269, 190) 到 (868, 484)。
# 列表区域以外的拜访字样（列表框边上下被遮住一半的行、其他面板）不算。
# 饭标识仍然全屏找，它在拜访标识上方，可能落在这块之外。
PURGE_DIR = Path("logs")
MARK_VISIT = "mark_visit"
MARK_FOOD = "mark_food"
PURGE_ROI = (0.2802, 0.3519, 0.9042, 0.8963)
FOOD_DY = (-46, -30)
FOOD_DX = (-14, 14)
PURGE_DX = -127            # 取关按钮在拜访标识左边 127 像素
PURGE_WAIT = 0.5           # 点完取关等列表重排再进下一轮识别
PURGE_IDLE = 20.0          # 连续这么久没识别到没饭的人就收工
PURGE_MAX = 100            # 一天最多取关多少人
MATCH_MIN_DIST = 10        # 多点匹配时两个命中点至少隔这么远，单位像素

# 卡住保护：同一个取关位置（横纵各允许漂移 PURGE_SAME_TOL 像素）连续点
# PURGE_SAME_MAX 次还认为需要取关，说明这个人点不掉（按钮被挡、弹窗、
# 卡网络），再点一百次也一样。忽略它翻一页绕过去，位置进忽略名单，
# 免得列表滑到底滑不动时又绕回来点它。
PURGE_SAME_MAX = 10
PURGE_SAME_TOL = 10

# 两种标识各用自己的阀值，都比 TH_DEFAULT 低。
# 饭标识在实测的那张图上只有 0.816 到 0.853，卡在 0.85 下面，
# 用默认阀值会把有饭的人误判成没饭然后取关掉，这个错没法挖回来。
# 阀值宁可定得松：饭标识认得越宽，误取关的风险越小。
TH_VISIT = 0.80
TH_FOOD = 0.75

# 滑动用的竖线。取列表右侧空白处，避开头像与按钮，不会误触。
# SWIPE_MS 是一次滑动耗的毫秒数。这个数越大，手指越慢，列表惯性越小。
# SWIPE_WAIT 是滑完等多久再截图。
SWIPE_X = 634
SWIPE_TOP_Y = 209
SWIPE_BOTTOM_Y = 472
SWIPE_MS = 150
SWIPE_WAIT = 0.5

# 清人翻页单独一套时序与行程，比找小耶时慢一倍、短一半，等得也更久。
# 清人要逐行看清每个人有没有饭标识，滑得快或滑得远都容易漏人。
# PURGE_SWIPE_DY 是一次翻页的行程，取找小耶那条竖线长度（263 像素）的
# 三分之一再除以二（约 87 像素）：行程短一点，每屏之间重叠更多，更不容易漏。
PURGE_SWIPE_MS = 300
PURGE_SWIPE_WAIT = 1.0
PURGE_SWIPE_DY = (SWIPE_BOTTOM_Y - SWIPE_TOP_Y) // 3

VISIT_PT = (567, 329)      # 坐标 B：拜访
UNFOLLOW_PT = (441, 330)   # 坐标 A：取关
NEXT_PT = (829, 504)       # 坐标 C：吃到之后直接跳下一家
NEXT_ALT_PT = (648, 140)   # 坐标 C 点不动时交替点的那一下

# 大重启：关注列表里卡住超过这么久还没处理一家，就把游戏整个重启。
# 跟世界频道那套（zuanfan_bot.STUCK_GAME_RESTART）一个时长。
# 卡住不等于某一家慢：LOADING_WAIT + NEXT_RETRY 这些每圈都要等
# 的时间本来就是常态，只有「从上次有进展起」的总时长超了才算卡。
STUCK_GAME_RESTART = 60.0    # 列表里卡多久就重启游戏，秒
GAME_RESTART_MAX = 3         # 一场最多重启几次，免得无人看着时反复重启

UNFOLLOW_WAIT = 0.8
LOADING_WAIT = 5.0
NEXT_RETRY = 8
NEXT_GAP = 0.25
ROUNDS_MAX = 300


def swipe(x1: int, y1: int, x2: int, y2: int, ms: int = SWIPE_MS) -> None:
    """从 (x1, y1) 滑到 (x2, y2)，耗时 ms 毫秒。跟点击一样打印出来，
    不然翻页那段时间控制台一片安静，看不出脚本在干嘛。"""
    print(f"  滑动 ({int(x1)}, {int(y1)}) → ({int(x2)}, {int(y2)})")
    core.sh("shell", "input", "swipe",
            str(int(x1)), str(int(y1)), str(int(x2)), str(int(y2)), str(int(ms)))


def page_down() -> None:
    """手指从上往下画。找小耶时一直用这一个方向。"""
    swipe(SWIPE_X, SWIPE_TOP_Y, SWIPE_X, SWIPE_BOTTOM_Y, SWIPE_MS)
    time.sleep(SWIPE_WAIT)


def page_up() -> None:
    """手指从下往上画。清人时用这一个方向，列表会往后走，露出更后面的玩家。

    只清人调这个函数，所以用 PURGE_SWIPE_MS、PURGE_SWIPE_WAIT、
    PURGE_SWIPE_DY 这套慢而短的参数。起点还是列表底那一端。
    """
    swipe(SWIPE_X, SWIPE_BOTTOM_Y,
          SWIPE_X, SWIPE_BOTTOM_Y - PURGE_SWIPE_DY, PURGE_SWIPE_MS)
    time.sleep(PURGE_SWIPE_WAIT)


def find_all(img, name, threshold=core.TH_DEFAULT, min_dist=MATCH_MIN_DIST):
    """全屏找出一个模板的所有命中点，返回 [(score, (cx, cy)), ...]。

    core.find 只给最高分那一个点，列表里一屏有好几个人，所以这里自己做一份。
    同一个标识周围会出一片分数都过阀值的像素，按分数从高到低扫一遍，
    距离已选点不到 min_dist 的丢掉，一个标识只留一个点。
    """
    tpl = core.template(name)
    src = core.prep(img, name)
    res = cv2.matchTemplate(src, tpl, cv2.TM_CCOEFF_NORMED)
    th, tw = tpl.shape[:2]

    ys, xs = np.where(res >= threshold)
    hits = sorted(
        ((float(res[y, x]), int(x) + tw // 2, int(y) + th // 2)
         for y, x in zip(ys, xs)),
        key=lambda h: h[0], reverse=True)

    out = []
    for score, cx, cy in hits:
        if any(abs(cx - px) < min_dist and abs(cy - py) < min_dist
               for _, (px, py) in out):
            continue
        out.append((score, (cx, cy)))
    return out


def find_all_roi(img, name, roi, threshold=core.TH_DEFAULT,
                 min_dist=MATCH_MIN_DIST):
    """只在 roi 这块里找模板，返回的坐标已经换回全图坐标。

    roi 是 (x0, y0, x1, y1) 四个比例。先裁下来再匹配，所以裁块必须比模板大。
    """
    h, w = img.shape[:2]
    x0, y0, x1, y1 = roi
    px0, py0 = int(x0 * w), int(y0 * h)
    px1, py1 = int(x1 * w), int(y1 * h)
    sub = img[py0:py1, px0:px1]

    hits = find_all(sub, name, threshold, min_dist)
    return [(score, (cx + px0, cy + py0)) for score, (cx, cy) in hits]


def first_no_food(img):
    """找出画面上第一个没饭标识的人，返回它的拜访标识中点。都有饭就返回 None。

    拜访标识只在 PURGE_ROI 这块里找。饭标识全屏找，它在拜访标识上方
    四十几像素，可能落到 PURGE_ROI 上边以外。

    不能直接在拜访标识旁边那个小窗口里单独跑一次匹配：窗口只有十几像素宽，
    比饭标识的模板还小，区域比模板小的时候匹配直接返回 0 分，
    所有人都会被当成没饭。

    按纵坐标从上往下取第一个，让行为稳定。
    """
    visits = find_all_roi(img, MARK_VISIT, PURGE_ROI, TH_VISIT)
    foods = find_all(img, MARK_FOOD, TH_FOOD)

    for _, (vx, vy) in sorted(visits, key=lambda h: h[1][1]):
        has_food = any(
            vx + FOOD_DX[0] <= fx <= vx + FOOD_DX[1]
            and vy + FOOD_DY[0] <= fy <= vy + FOOD_DY[1]
            for _, (fx, fy) in foods)
        if not has_food:
            return (vx, vy)
    return None


def purge_flag() -> Path:
    """今天这个号的标记文件路径。按游戏日分天：凌晨 5 点
    （core.GAME_DAY_RESET_HOUR）之前还算前一天，因为游戏的每日额度
    5 点才刷新。文件名带账号名（user.py），两个号同一天各清各的。"""
    return PURGE_DIR / f"purge_{core.game_day()}_{user.current()['name']}.flag"


def purged_today() -> bool:
    """今天清过一遍没有。读不了目录时当成清过了，宁可少清不可误删人。"""
    try:
        return purge_flag().exists()
    except OSError:
        return True


def mark_purged() -> None:
    """写下今天已经清过的标记。写不进去不能打断跑圈，但要把话说清楚。"""
    try:
        PURGE_DIR.mkdir(exist_ok=True)
        purge_flag().write_text(time.strftime("%Y-%m-%d %H:%M:%S"),
                                encoding="utf-8")
    except OSError as exc:
        print(f"  !! 写不下今天已清的标记：{exc}。"
              f"今天再跑一次这个脚本会把已经吃过的人误取关")


def purge_no_food() -> int:
    """把列表里没饭标识的人逐个取关。返回取关了多少人。

    一轮只点一个人。取关后列表会重排，上一帧算出来的其他坐标全部作废，
    所以点完等 PURGE_WAIT 秒重新截图再算一遍。

    当前这一屏每个人都有饭的时候往后翻一页再看，而不是原地等。
    列表很长，没饭的人不一定在第一屏。翻页走 page_up，手指从下往上画。

    两个停止条件：连续 PURGE_IDLE 秒没识别到没饭的人，或者取够 PURGE_MAX 人。
    计时在每次取关成功后重置。滑到底之后画面不再变，空转满时间就停。

    卡住保护：同一个取关位置（横纵各漂移 PURGE_SAME_TOL 像素内）连点
    PURGE_SAME_MAX 次还没取关掉，就把这个位置拉进黑名单，翻一页绕过去。
    黑名单里的位置这场清理不再点。反例也想过了：连着一串没饭的人会让
    取关按钮反复出现在同一行，连点十次是可能的——但那只是多翻一页，
    今天少清几个，比对着一个卡死的人点满 PURGE_MAX 次强。

    缺模板时不取关也不报错，直接返回 -1，让调用方知道这一步根本没跑成。
    """
    dropped = 0
    swipes = 0
    last_hit = time.time()
    last_tap = None      # 上一次点的取关位置，连着点同一处才算卡住
    same_taps = 0        # 连着点同一处的次数
    blocked = []         # 卡死过的位置，这场清理里不再点

    while dropped < PURGE_MAX:
        if time.time() - last_hit >= PURGE_IDLE:
            print(f"  连续 {PURGE_IDLE:.0f} 秒没见到没饭的人，清理收工"
                  f"（期间翻了 {swipes} 页）")
            break

        try:
            pt = first_no_food(core.screenshot())
        except FileNotFoundError as exc:
            print(f"  {exc}，跳过今天的清理")
            return -1

        if pt is None:
            swipes += 1
            page_up()
            continue

        tap_pt = (pt[0] + PURGE_DX, pt[1])

        # 卡住保护。两种翻过这一页的情形都不重置 last_hit：
        # 没点成就不算进展，真卡死时 PURGE_IDLE 会把这场清理财掉
        if any(abs(tap_pt[0] - bx) <= PURGE_SAME_TOL
               and abs(tap_pt[1] - by) <= PURGE_SAME_TOL
               for bx, by in blocked):
            swipes += 1
            page_up()
            continue
        if last_tap is not None and (
                abs(tap_pt[0] - last_tap[0]) <= PURGE_SAME_TOL
                and abs(tap_pt[1] - last_tap[1]) <= PURGE_SAME_TOL):
            same_taps += 1
        else:
            same_taps = 0
        if same_taps >= PURGE_SAME_MAX:
            blocked.append(tap_pt)
            print(f"  取关位置 {tap_pt} 连点 {same_taps} 次还没取关掉，"
                  f"当卡死处理，翻页绕过")
            swipes += 1
            page_up()
            continue

        core.tap(*tap_pt)
        last_tap = tap_pt
        dropped += 1
        last_hit = time.time()
        if core.VERBOSE:
            print(f"  取关第 {dropped} 人，拜访标识 @ {pt}"
                  f"，点 {tap_pt}")
        time.sleep(PURGE_WAIT)

    if dropped >= PURGE_MAX:
        print(f"  已取关 {PURGE_MAX} 人，到上限了，清理收工")
    return dropped


def daily_purge() -> bool:
    """今天还没清过就清一遍。返回这次是不是真的清了。

    只能在改排序之前调。清完之后才轮到校排序、读关注人数、开始吃饭。
    返回值给 open_list 用：刚清完说明列表刚取关了一批人，
    小耶可能沉到很靠下，找她的滑动次数上限要放宽。
    """
    if purged_today():
        if core.VERBOSE:
            print("  今天已经清过一遍没饭的人，跳过")
        return False

    print("  今天第一次开工，先把没饭标识的人清一遍")
    dropped = purge_no_food()
    if dropped < 0:
        return False
    mark_purged()
    print(f"  清理完成，共取关 {dropped} 人")
    return True


def order_scores(img=None):
    """两张排序模板在 ORDER_ROI 里的分数。缺模板的那张返回 None。"""
    if img is None:
        img = core.screenshot()
    out = {}
    for name in ("correctorder", "wrongorder"):
        try:
            score, _ = core.find(img, name, ORDER_ROI, threshold=0.0)
        except FileNotFoundError:
            out[name] = None
            continue
        out[name] = score
    return out


def friends_score(img=None):
    """好友页标题在 FRIENDS_ROI 里的分与位置。缺模板时返回 (None, None)。"""
    if img is None:
        img = core.screenshot()
    try:
        return core.find(img, FRIENDS_ANCHOR, FRIENDS_ROI, threshold=0.0)
    except FileNotFoundError:
        return None, None


def see_friends(img=None) -> bool:
    """在不在好友页。缺模板时当作不在，不报错。"""
    score, _ = friends_score(img)
    if score is None:
        return False
    return score >= core.TH_DEFAULT


def close_friends() -> None:
    """点左上角的好友页标题（返回）收掉好友页，等标题锚点消失。

    好友页开着的时候，右下角回家那两下的位置被面板占着。所以回自己
    家之前要先把好友页收掉；标题锚点消失才算真收掉了。缺模板时
    没法确认，点一下写死的标题中心就走，跟以前不靠确认的点击
    一个风险等级。
    """
    for _ in range(3):
        score, pt = friends_score()
        if score is None:
            core.tap(*FRIENDS_BACK_PT)
            time.sleep(HOME_GAP)
            return
        if score < core.TH_DEFAULT:
            return                        # 已经不在好友页了
        core.tap(*pt)
        deadline = time.time() + core.WORLD_TAP_TIMEOUT
        while time.time() < deadline:
            if not see_friends():
                return
            time.sleep(core.POLL)
    print("  点了 3 下好友页标题还没收掉好友页")


def anchor_score(img=None):
    """小耶名片在全屏的最高分与位置。缺模板时返回 (None, None)。"""
    if img is None:
        img = core.screenshot()
    try:
        return core.find(img, ANCHOR, threshold=0.0)
    except FileNotFoundError:
        return None, None


def see_anchor(img=None) -> bool:
    """画面上有没有小耶的名片。缺模板时当作没有，不报错。"""
    score, _ = anchor_score(img)
    if score is None:
        return False
    return score >= core.TH_DEFAULT


def to_anchor(swipe_max: int = ANCHOR_SWIPE_MAX) -> bool:
    """往下滑直到看见小耶的名片。已经看得见就什么都不做。

    swipe_max 是最多滑几次。默认 ANCHOR_SWIPE_MAX（25）；刚清完
    列表传 PURGE_SWIPE_MAX（100），清掉一批人之后小耶可能沉到
    很靠下，25 次滑不到。
    """
    for i in range(swipe_max + 1):
        score, pt = anchor_score()
        if score is None:
            print(f"  缺 {ANCHOR} 模板，没法找小耶")
            return False
        # 每次滑动的分数太长，归 --debug。滑很多次还没找到时
        # 下面有结论性的失败消息，不会无声无息
        if core.DEBUG:
            print(f"  第 {i} 次：{ANCHOR} {score:.3f} @ {pt}")
        if score >= core.TH_DEFAULT:
            return True
        if i < swipe_max:
            page_down()

    print(f"  下滑 {swipe_max} 次还是没找到小耶的名片")
    return False


def check_order() -> bool:
    """确保料理排序是对的。返回最后是不是对的。

    两张模板拍的是同一个位置的同一个控件，只差几个字，所以两张在两种
    状态下都能拿到 0.85 以上。不能拿 correctorder 单独比阀值，要比两张的
    分数高低：correctorder 高出 SORT_MARGIN 才算排序对。
    """
    for i in range(SORT_RETRY):
        s = order_scores()
        c, w = s["correctorder"], s["wrongorder"]
        # 分数本身太长了，属于匹配分数类的诊断输出，归 --debug；
        # 平时只留「改没改对」这种结论性的短消息
        if core.DEBUG:
            print(f"  第 {i + 1} 轮：correctorder {c} / wrongorder {w}")

        if c is None or w is None:
            print("  排序模板不全，没法判排序")
            return False
        if c - w >= SORT_MARGIN:
            if i:
                print(f"  排序已改对（点了 {i} 轮）")
            return True

        print(f"  排序不对，点两下改成料理（第 {i + 1} 轮）")
        for pt in SORT_PTS:
            core.tap(*pt)
            time.sleep(SORT_GAP)

    print(f"  试了 {SORT_RETRY} 轮排序还是不对")
    return False


def open_friends() -> bool:
    """打开好友页，用左上角标题确认真的开到了。

    先看一眼再点：已经在好友页就一下都不点，避开把已经开好的页反而关掉。
    没开到就重新把 P1 P2 两下完整重放一遍，不是只补点 P2。

    前两轮重放还没开到时，多半是画面上有东西挡着（派对、弹窗），
    P1 点在了空气里。这种时候先点左上角 HOME_PT 收掉那层遮挡，
    再接着重放。HOME_PT 在主界面上点一下没坏处，在派对里正好退出。
    """
    for i in range(RETRY_MAX + 1):
        score, _ = friends_score()
        if score is None:
            print(f"  缺 {FRIENDS_ANCHOR} 模板，没法判好友页")
            return False
        # 分数属于匹配分数类的诊断输出，归 --debug。平时只留
        # 「重放了几轮还没开到」这种要采取动作的结论
        if core.DEBUG:
            print(f"  好友页第 {i} 次：{FRIENDS_ANCHOR} {score:.3f}")
        if score >= core.TH_DEFAULT:
            return True
        if i < RETRY_MAX:
            if i == 2:
                print("  P1 P2 连点两轮没开到，可能有遮挡，"
                      "先点左上角收一下再重试")
                core.tap(*HOME_PT)
                time.sleep(HOME_GAP)
            core.tap(*OPEN_PTS[0])
            time.sleep(OPEN_GAP)
            core.tap(*OPEN_PTS[1])
            time.sleep(OPEN_GAP)

    print(f"  点 P1 P2 重放 {RETRY_MAX} 轮还没开到好友页")
    return False


def open_list(purge: bool = False) -> bool:
    """打开关注列表，并把界面整理到可以按死坐标操作的状态。

    六步，缺一步坐标就会点错人：
        1. 点 P1 P2 开好友页，用好友页标题确认。
        2. 点 P3 切到关注列表。没看到小耶就只补点 P3，最多 RETRY_MAX 次。
        3. 补点到顶还没看到小耶时，往下滑找一找。
        4. purge 为真且今天还没清过时，把没饭标识的人全取关掉。
           这一步必须在改排序之前。
        5. 校正料理排序。重开一次关注页排序就可能回到默认，所以每次都校。
        6. 改排序会把列表重排，小耶会换位置，所以再滑找一遍她。

    判「在没在关注页」只拿小耶的名片。
    """
    if not open_friends():
        return False

    core.tap(*OPEN_PTS[2])
    time.sleep(OPEN_GAP)

    opened = False
    for i in range(RETRY_MAX):
        if see_anchor():
            if i:
                print(f"  补点 {i} 次后看到小耶")
            opened = True
            break
        core.tap(*RETRY_PT)
        time.sleep(RETRY_GAP)

    if not opened:
        print(f"  补点 {RETRY_MAX} 次还没看到小耶，往下滑找一找")
        if not to_anchor():
            return False

    if purge:
        purged_now = daily_purge()
    else:
        purged_now = False

    if not check_order():
        return False

    # 刚清完列表时小耶可能沉到很靠下，滑动上限放宽到 100 次
    return to_anchor(PURGE_SWIPE_MAX if purged_now else ANCHOR_SWIPE_MAX)


def read_total(img=None):
    """从关注页读关注人数。读不出来就返回 None，让调用方改成手输。

    区域里写的是「关注数：x/400」，要的是斜线前面那个 x，不是 400。
    返回的是屏幕上的原数，不减。要吃几家由 plan_total 算。
    依赖 pytesseract 与 Tesseract 本体，两者少一个就读不出来。
    找 tesseract.exe 这一步跟读晶钻数用的是同一份，在 diamonds.py 里。
    """
    if TOTAL_ROI is None:
        return None
    try:
        import pytesseract
    except ImportError:
        print("  没装 pytesseract，读不了关注人数")
        return None

    if not diamonds.setup_tesseract(pytesseract):
        print("  找不到 tesseract.exe。试过的路径：")
        for path in diamonds.TESSERACT_DIRS:
            print(f"    {path}")
        return None

    if img is None:
        img = core.screenshot()
    h, w = img.shape[:2]
    x0, y0, x1, y1 = TOTAL_ROI
    crop = img[int(y0 * h):int(y1 * h), int(x0 * w):int(x1 * w)]
    try:
        text = pytesseract.image_to_string(
            crop, config="--psm 7 -c tessedit_char_whitelist=0123456789/")
    except Exception as e:
        print(f"  OCR 失败：{e}")
        return None

    m = re.search(r"(\d+)\s*/", text)
    if not m:
        m = re.search(r"(\d+)", text)
    if not m:
        print(f"  OCR 没读到数字，原文：{text!r}")
        return None
    n = int(m.group(1))
    print(f"  OCR 读到关注人数：{n}（原文 {text.strip()!r}）")
    return n


def ask_total():
    """关注人数 OCR 没读出来时问人。面板里走弹窗，命令行走控制台；
    都没接上就回退到直接 input（此时多半就是命令行在跑）。
    直接回车/取消表示不限人数，返回 None。"""
    if diamonds.ASK is not None:
        return diamonds.ASK("没识别到关注人数。看一眼关注页右上角"
                            "「关注数：x/400」，输入斜线前的那个数"
                            "（取消 = 不限人数跑）：")
    try:
        raw = input("关注人数（直接回车跳过）：").strip()
    except EOFError:
        return None
    return int(raw) if raw.isdigit() else None


def plan_total(raw):
    """把关注人数换成要去吃饭的家数。raw 为 None 时原样返回 None。

    关注数直接就是要处理的家数，不扣任何名片。
    """
    if not raw:
        return None
    return max(0, raw)


def wait_loading(secs: float = LOADING_WAIT) -> bool:
    """看 secs 秒内出不出得来加载页。

    点完拜访后这么久没出来，就是对方设了拜访权限，这一下根本没生效。
    """
    deadline = time.time() + secs
    while time.time() < deadline:
        if enter_wait.in_loading(core.screenshot()):
            return True
        time.sleep(core.POLL)
    return False


def retry_next() -> bool:
    """点过坐标 C 没出加载页时，交替点坐标 C 与 NEXT_ALT_PT。

    一共点 NEXT_RETRY 下，两个点各分一半，每下之后只看一眼有没有进加载页。
    这里只看一帧，不走 wait_loading 的整段等待。
    """
    for k in range(NEXT_RETRY):
        pt = NEXT_PT if k % 2 == 0 else NEXT_ALT_PT
        print(f"  点坐标 C 没出加载页，重点第 {k + 1} 次 @ {pt}")
        core.tap(*pt)
        time.sleep(NEXT_GAP)
        if enter_wait.in_loading(core.screenshot()):
            return True
    return False


def wait_loaded() -> bool:
    """已经看到加载页了，等它消失。加载多久都等，只留一道硬上限。"""
    hard = time.time() + enter_wait.TIMEOUT_TOTAL
    while time.time() < hard:
        if not enter_wait.in_loading(core.screenshot()):
            return True
        time.sleep(core.POLL)
    if core.VERBOSE:
        print(f"  加载页卡了 {enter_wait.TIMEOUT_TOTAL:.0f} 秒没过去")
    return False


def eat_here() -> bool:
    """已经进门了，扫三星并把虫抓完。返回是不是真吃到了。"""
    stars = enter_wait.scan_home()
    if not stars:
        return False
    _, why = core.catch_all_bugs(stars)
    return why == "clean"


def where_after_meal(img=None) -> str:
    """吃完饭后认一眼人现在在什么画面上，返回 "party" / "home" / "lost"。

    判据与对应的走法：
        party  core.PARTY_ROI 里看到「下一家」按钮（xiayijia 模板）。
               这个按钮只在开派对的人家里出现。派对里右下角回家那
               两下和坐标 C 都点不动，离开只能点左上角返回。
        home   没有下一家，但顶部蓝色晶钻图标在。晶钻图标在主界面和
               家园里常驻，它在说明画面是正常的家园，照旧走坐标 C。
               注意只能这么用：晶钻图标自己家里也有，不能拿它当
               「在别人家」的判据；这里只用它的缺席来发现画面不对。
        lost   两个都看不到。弹窗或别的异常界面，也点左上角返回兜底。

    缺模板时按 "home" 处理：维持改动前的老行为，宁可漏认派对
    （后面还有坐标 C 点不动那条老路兜着），不添错误的新动作。
    """
    if img is None:
        img = core.screenshot()
    try:
        _, pt = core.find(img, core.PARTY_TPL, core.PARTY_ROI)
    except FileNotFoundError as exc:
        print(f"  {exc}，认不了派对，按普通家园处理")
        return "home"
    if pt:
        return "party"
    try:
        _, dia_pt = core.find(img, diamonds.TPL, diamonds.ICON_ROI)
    except FileNotFoundError as exc:
        print(f"  {exc}，没法确认晶钻图标，按普通家园处理")
        return "home"
    return "home" if dia_pt else "lost"


def leave_party() -> bool:
    """点左上角返回（退出派对），等回家的加载页过去。返回有没有看到加载页。

    两种情况下调：认出了派对（看到「下一家」按钮），或者饭后画面上
    「下一家」和晶钻图标都没有、状态不认识，点它兜底。平时不要用
    这一下，它不一定能回家。
    """
    core.tap(*HOME_PT)
    time.sleep(HOME_GAP)
    if wait_loading(RESTART_LOADING_WAIT):
        print("  看到回家的加载页，等它过去")
        wait_loaded()
        return True
    return False


def restart_taps() -> None:
    """依次点 RESTART_PTS 里的两下，中间隔 RESTART_GAP 秒，再等回家。

    这两下会把人送回自己家，中间要过一遍加载页。加载页还挡着的时候
    点 P1 P2 一定落空，所以这里先把加载页等过去。
    人本来就在自己家的时候不能调这个函数，那两下会进到别的页面卡住。
    """
    for pt in RESTART_PTS:
        core.tap(*pt)
        time.sleep(RESTART_GAP)
    if wait_loading(RESTART_LOADING_WAIT):
        print("  看到回家的加载页，等它过去")
        wait_loaded()


def restart_to_list(away: bool = True) -> bool:
    """回到自己家，再打开关注页。最多试 RESTART_TRIES 次。

    away 说的是人现在在不在别人家里。在别人家里才点右下角两下回家。
    """
    for i in range(RESTART_TRIES):
        if away or i:
            print(f"  点右下角两下回家再重开关注页（第 {i + 1} 次）")
            restart_taps()
        else:
            print("  人就在自己家，不点右下角，直接开关注页")
        if open_list():
            return True
    return False


def drop_and_next() -> bool:
    """取关当前这一位，再拜访下一位。返回操作是不是走通了。"""
    if not open_list():
        return False
    return unfollow_and_visit()


def unfollow_and_visit() -> bool:
    """已经回到整理好的关注页，点取关再点拜访。

    点完取关要先等 UNFOLLOW_WAIT 秒再识别。取关后列表要重排，
    点完立即截图拍到的还是旧画面。
    两件事都要查：排序还是不是料理，小耶还在不在。先校排序。
    """
    core.tap(*UNFOLLOW_PT)
    time.sleep(UNFOLLOW_WAIT)
    if not check_order():
        print("  取关后排序没改回料理")
        return False
    if not see_anchor():
        print("  取关后看不到小耶，滑找一找")
        if not to_anchor():
            return False
    core.tap(*VISIT_PT)
    return True


def reopen_and_visit() -> bool:
    """先点左上角退出派对，重新打开关注页，直接点拜访，不取关。

    这条路只在「吃到了但坐标 C 跳不走」时走，原因是对方家里开着派对。
    左上角那一下没生效时，再退回用右下角两下的老办法。
    """
    leave_party()
    if open_list():
        core.tap(*VISIT_PT)
        return True

    print("  左上角退出后没能打开关注页，改用右下角两下")
    restart_taps()
    if not open_list():
        return False
    core.tap(*VISIT_PT)
    return True


def run(total=None, dia: diamonds.Tracker | None = None,
        should_stop=None, paused=None, parked=None,
        stats: dict | None = None) -> tuple[int, bool]:
    """跑一遍关注列表。返回 (吃到的家数, 计划是否处理完)。

    计划处理完 = 正常吃到计划家数、处理完所有该处理的，或者晶钻满了、
    用户收了工。因为打不开好友页/重启失败提前中断时是 False——
    这时候转世界频道没道理：列表里明明还有饭没吃，列表吃得比世界
    频道靠谱。调用方（main / 面板）拿 False 时直接收工，让人去看一眼
    模拟器是不是卡住了。

    total 是屏幕上的关注人数。不给就先 OCR，读不出来再问。
    拿到之后过 plan_total 减掉不去吃的那一个，得到要处理的家数。

    dia 是晶钻计数器。不给就自己造一个。开工时读一次晶钻数，
    每吃到一家再读一次并写进晶钻日志。

    should_stop 是面板传进来的「用户点了收工」检查，paused 是
    「用户点了暂停」检查，都在每一圈开头看。暂停在当前这家收尾后
    生效——一家跑到一半停下，回来坐标就全乱了。parked 是「真的
    停住了」的回执：进入暂停空转时调 parked(True)，离开时调
    parked(False)，面板拿它区分「暂停中」和「已暂停」，没看到
    True 之前不敢说已暂停。stats 给了就每圈往里写晶钻三个键
    （dia_start/dia_now/dia_gain），面板的晶钻栏读它们。
    命令行直接跑时这些都不给，跟以前一样跑到底。

    这个函数不管开不开世界频道，那个判断在 main 里做。

    开场：打开关注页（含每日清理、校正排序、滑找小耶），然后点一下拜访。
    每日清理只在当天第一次启动时跑，而且一定在改排序之前。

    之后每一圈都从「刚点过一下」开始，三种走向：
        没出加载页   对方设了拜访权限。回自己家，重开关注页，取关它，拜访下一位
        进门没吃到   家里没饭。取关它，拜访下一位
        进门吃到了   留着关注。点坐标 C 直接跳下一家

    每走完 HOME_EVERY（10）个人必须回一次自己家再继续：吃到了就不点
    坐标 C，直接点右下角两下回家；没吃到就先取关，点左上角好友页标题
    返回（收掉好友页，不然右下角那两下点在面板上），再点右下角两下
    回家。到家后重新打开关注列表点拜访。计划家数已经凑够时不折腾这趟。

    回家靠的是点右下角那两下，所以要知道人现在在哪。away 记的就是这件事。
    人在自己家时不能点那两下，点了会进到别的页面卡住。
    左上角返回那一下给两种场面用：饭后认出了派对（右下角回家和坐标 C
    在派对里都点不动），或饭后「下一家」和晶钻图标都看不到时兜底。
    饭后识别在 where_after_meal，见它的注释。

    两道结束条件：处理完计划的家数，或者晶钻比开工时多出 DAILY_LIMIT 颗。
    后一道是游戏的硬上限，吃满了再跑都是白跑。吃满时人还在别人家里，
    先点右下角两下回自己家再退出，游戏不会停在别人家里这种半吊子状态；
    面板上跑时回完家还会弹窗问要不要继续刷（问人在 panel.py 的
    run_flow 里，这里只负责回家和上报）。
    """
    core.connect()

    dia = dia or diamonds.Tracker("list")

    def pub_dia() -> None:
        """把晶钻三个键写进 stats 给面板显示。空串 = 没读到/没计数。"""
        if stats is None:
            return
        stats.update(
            dia_start="" if dia.start is None else dia.start,
            dia_now="" if dia.last is None else dia.last,
            dia_gain="" if dia.gained() is None else dia.gained())

    restarts = 0
    away = False    # 人在不在别人家里。开场在自己家
    eaten = 0       # 吃到饭的家数
    dropped = 0     # 取关掉的人数
    entered = 0     # 看到加载页的次数，也就是真进门的家数
    next_home_at = HOME_EVERY    # 处理完这么多人就必须回一次自己家

    def try_restart() -> bool:
        """自我重启直到回到整理好的关注页。返回最后成没成。"""
        nonlocal restarts, away
        while restarts < RESTART_MAX:
            restarts += 1
            print(f"  自我重启第 {restarts} 轮")
            if away or restarts > 1:
                restart_taps()
                away = False
            if open_list(purge=True):
                return True
        print(f"自我重启 {RESTART_MAX} 轮都没成")
        return False

    def drop_and_home() -> bool:
        """这家没吃到：取关它，收掉好友页，回自己家，重开关注页再拜访。

        每走完 HOME_EVERY 个人的收尾走这条路，跟平时的 drop_and_next
        只差在不直接拜访下一位，而是先回一趟自己家。取关动作不变：
        打开关注列表点坐标 A，然后点左上角好友页标题返回——好友页
        不收掉的话，右下角回家那两下会点在面板上。
        """
        nonlocal away
        if not open_list():
            return False
        core.tap(*UNFOLLOW_PT)
        time.sleep(UNFOLLOW_WAIT)
        close_friends()
        restart_taps()
        away = False
        if not open_list():
            return False
        core.tap(*VISIT_PT)
        return True

    # 晶钻数只在主界面和家园里显示，关注列表一打开就被面板挡住读不到了。
    # 所以开工读数必须抢在 open_list 之前，这时候人还在主界面。
    dia.begin()
    pub_dia()

    # 起点可能是从今天的日志里恢复的：已经吃满就别开工了，
    # 连关注列表都不用打开
    if dia.full():
        core.print_blue(f"晶钻已经比起点多出 {dia.gained()} 颗，"
                        f"今天的 {diamonds.DAILY_LIMIT} 口吃满了，不开工")
        return eaten, True

    purged_before = purged_today()
    if not open_list(purge=True):
        print("关注页没整理好（没开到 / 滑不到小耶 / 排序没改对），试自我重启")
        if not try_restart():
            return eaten, False

    if total is None:
        total = read_total()
    if total is None:
        total = ask_total()
    plan = plan_total(total)
    if plan:
        # 今天已经吃过的家饭标识已经没了。这次开跑清过列表的话，
        # 他们刚被取关，OCR 读到的人数里本来就不含他们，不能再减；
        # 没清的话他们还在名单里，不用再处理，从计划里减掉。
        did_purge = not purged_before
        already = 0
        if not did_purge and dia is not None and dia.enabled:
            # 「今天已吃过几家」有两个来源，取大的那个：
            #   100 − 上限   用户把「满__颗时问我」调小了，差额是他
            #                声明的已吃颗数（手工吃过、没有日志时靠它）
            #   gained()     起点没变（基线/日志恢复）时，比开工时多出
            #                的就是今天已经吃的。上限现在默认一直是 100，
            #                没有这一项的话，同一天重开脚本会把已吃过的
            #                家再拜访一遍，白跑还把人取关了
            already = max(0, diamonds.GAME_DAILY_MAX - diamonds.DAILY_LIMIT,
                          dia.gained() or 0)
        if already:
            plan = max(0, plan - already)
        print(f"关注 {total} 人，减掉今天已吃过的 {already} 家，"
              f"计划处理 {plan} 家")
    else:
        print("没给人数，不限人数跑")

    core.tap(*VISIT_PT)
    last = "visit"

    completed = True      # 计划是不是真跑完了，重启失败中断时改 False
    pause_noted = False
    game_restarts = 0              # 大重启（重启整个游戏）的次数
    last_progress = time.time()    # 上一次处理完一家的时刻
    prev_done = -1
    for i in range(ROUNDS_MAX):
        # 暂停在当前这家收尾后才生效。等暂停的时候也要看着收工，
        # 不然暂停状态下点收工会一直干等。
        # 进空转了才报 parked(True)：这一刻起脚本真的不点屏幕了，
        # 面板这才敢显示「已暂停」
        while paused is not None and paused():
            if not pause_noted:
                pause_noted = True
                print("已暂停（当前这家跑完后停住），点「继续」恢复")
                if parked is not None:
                    parked(True)
            if should_stop and should_stop():
                break
            time.sleep(0.3)
        if pause_noted:
            pause_noted = False
            if parked is not None:
                parked(False)

        if should_stop and should_stop():
            print("面板点了收工，列表提前结束")
            break

        pub_dia()

        done = eaten + dropped
        if done != prev_done:
            prev_done = done
            last_progress = time.time()

        # 大重启：处理完一家的计数卡住两分钟没动，多半是游戏整个卡死了
        # （好友页/关注页怎么点都没反应那种），自我重启那点页面操作
        # 救不回来。跟世界频道那套一个思路：force-stop 重开再打开列表。
        if time.time() - last_progress > STUCK_GAME_RESTART:
            if game_restarts >= GAME_RESTART_MAX:
                print(f"  大重启 {GAME_RESTART_MAX} 次都没救回来，收工")
                completed = False
                break
            game_restarts += 1
            print(f"  列表卡了 {STUCK_GAME_RESTART:.0f} 秒没进展，"
                  f"大重启游戏（第 {game_restarts} 次）")
            if not core.restart_game():
                print("  大重启没成功（多半是模拟器卡死），收工")
                completed = False
                break
            away = False
            restarts = 0            # 游戏都是新开的，自我重启计数也清零
            if not open_list(purge=True):
                print("  大重启后还是打不开关注页，试自我重启")
                if not try_restart():
                    completed = False
                    break
            core.tap(*VISIT_PT)
            last = "visit"
            last_progress = time.time()
            continue

        if plan and done >= plan:
            print(f"已处理 {done} 家，够 {plan} 家了")
            break

        if not wait_loading():
            if last == "visit":
                if core.VERBOSE:
                    print(f"  第 {i + 1} 位：{LOADING_WAIT:.0f} 秒没出加载页，没拜访权限")
                ok = restart_to_list(away=away)
                away = False
                if not ok:
                    print(f"  重开关注页失败（已处理 {done} / {plan}），试自我重启")
                    if not try_restart():
                        completed = False
                        break
                if not unfollow_and_visit():
                    print("  取关后没能回到可拜访的状态，试自我重启")
                    if not try_restart():
                        completed = False
                        break
                    core.tap(*VISIT_PT)
                dropped += 1
                last = "visit"
                continue

            # 刚才点的是坐标 C，人还在别人家里。先多点几次，再回家重来。
            # 对方在开派对时坐标 C 怎么点都没用，只能靠 reopen_and_visit
            # 里的左上角那一下退出。
            if not retry_next():
                if not plan:
                    print("坐标 C 点不动了，没给人数没法接着判，当列表跑完")
                    break
                print(f"  坐标 C 点不动，当成派对，点左上角退出（已处理 {done} / {plan}）")
                ok = reopen_and_visit()
                away = False
                if not ok:
                    print("  重新打开关注页失败，试自我重启")
                    if not try_restart():
                        completed = False
                        break
                    core.tap(*VISIT_PT)
                last = "visit"
                continue

        entered += 1
        away = True

        if not wait_loaded():
            print("  加载页一直不消失，试自我重启")
            if not try_restart():
                completed = False
                break
            core.tap(*VISIT_PT)
            last = "visit"
            continue

        if eat_here():
            eaten += 1
            if core.VERBOSE:
                print(f"  第 {i + 1} 位：吃到了，跳下一家")
            # 晶钻读数写日志，只用来事后算准确率和判当天的硬上限，
            # 不拿它判这一家该不该接着吃。
            dia.after_meal(eaten)
            pub_dia()

            # 认一眼现在的画面：派对和异常界面都在这一步处理。
            # 晶钻满的回自己家收尾也在派对分支里，不能用右下角那两下
            where = where_after_meal()
            if where != "home":
                if where == "party":
                    if core.VERBOSE:
                        print("  看到「下一家」，这家在开派对，点左上角返回")
                else:
                    print("  「下一家」和晶钻图标都没看到，画面不认识，"
                          "点左上角返回兜底")
                leave_party()
                away = False
                if not open_list():
                    print("  左上角返回后重开关注页失败，试自我重启")
                    if not try_restart():
                        completed = False
                        break
                # 这趟已经回过家、重开过列表，跟每 HOME_EVERY 家回一趟
                # 等效。越过的门槛记掉，不然下一家马上又白回一趟
                if eaten + dropped >= next_home_at:
                    next_home_at += HOME_EVERY
                if dia.full():
                    core.print_blue(
                        f"晶钻已经比开工时多出 {diamonds.DAILY_LIMIT} 颗，"
                        f"今天的 {diamonds.DAILY_LIMIT} 口吃满了，"
                        f"人已回自己家，收工")
                    break
                core.tap(*VISIT_PT)
                last = "visit"
                continue

            if dia.full():
                core.print_blue(
                    f"晶钻已经比开工时多出 {diamonds.DAILY_LIMIT} 颗，"
                    f"今天的 {diamonds.DAILY_LIMIT} 口吃满了，"
                    f"点右下角两下回自己家")
                # 这一刻人还站在别人家里，RESTART_PTS 那两下正好用得上。
                # 回完家再退出，游戏停在干净的自己家里；面板上跑时
                # 接着还会弹窗问要不要继续刷（在 run_flow 里）
                restart_taps()
                away = False
                break
            if (eaten + dropped >= next_home_at
                    and (not plan or eaten + dropped < plan)):
                # 每走完 HOME_EVERY 个人回一次家：这家吃到了，
                # 不点坐标 C 跳下一家，直接点右下角两下回自己家，
                # 再重新打开关注列表接着吃。计划已经够了就不折腾这趟
                next_home_at += HOME_EVERY
                print(f"  已处理 {eaten + dropped} 家，回自己家一趟再继续")
                restart_taps()
                away = False
                if not open_list():
                    print("  回家后重开关注页失败，试自我重启")
                    if not try_restart():
                        completed = False
                        break
                core.tap(*VISIT_PT)
                last = "visit"
                continue
            core.tap(*NEXT_PT)
            last = "next"
            continue

        if core.VERBOSE:
            print(f"  第 {i + 1} 位：进门了但没吃到，取关")
        if (eaten + dropped + 1 >= next_home_at
                and (not plan or eaten + dropped + 1 < plan)):
            # 每走完 HOME_EVERY 个人回一次家：这家没吃到，取关完
            # 不直接拜访下一位，收掉好友页先回一趟自己家
            next_home_at += HOME_EVERY
            print(f"  已处理 {eaten + dropped + 1} 家，回自己家一趟再继续")
            ok = drop_and_home()
        else:
            ok = drop_and_next()
        if not ok:
            print(f"  重新打开关注页或取关失败（已处理 {done} / {plan}），试自我重启")
            if not try_restart():
                completed = False
                break
            if not unfollow_and_visit():
                completed = False
                break
        dropped += 1
        last = "visit"

    # 没给人数时没法判「处理完没有」，只有明摆着中断才算没跑完。
    # 晶钻满了主动收工不算中断：那是正常结束的一天，不是卡住了
    if plan and (eaten + dropped) < plan and not dia.full():
        completed = False
    print(f"列表跑完：进门 {entered} 家，吃到 {eaten} 家，取关 {dropped} 人，"
          f"自我重启 {restarts} 轮，大重启 {game_restarts} 次"
          f"{'' if completed else '（计划没处理完，提前中断）'}")
    return eaten, completed


def main() -> None:
    """先跑关注列表，再按吃到的口数决定要不要转世界频道。

    三种收尾：
        晶钻比开工时多出 DAILY_LIMIT 颗：当天吃满了，直接收工。
        列表里就吃满 EAT_TARGET 口：收工，世界频道也不开。
        都没到：开面板接着在世界频道找饭，把列表里吃到的口数和晶钻起点
        一起传过去，两段合起来算一笔账。
    """
    p = argparse.ArgumentParser(description="第四档：关注列表巡回")
    p.add_argument("--total", type=int, default=None,
                   help="关注人数。不给就先试 OCR，读不出来再问")
    p.add_argument("--target", type=int, default=EAT_TARGET,
                   help=f"一天总共要吃多少口，默认 {EAT_TARGET}")
    p.add_argument("--no-world", action="store_true",
                   help="列表跑完就停，不转世界频道")
    args = p.parse_args()

    # 命令行没有弹窗可用，开工读数失败时问人走控制台输入
    diamonds.ASK = diamonds.console_ask

    # 当前账号的三项开工设置（user.py）。其中「重新识别晶钻」和「手工吃
    # 几口」不用在这里动手，diamonds.Tracker.begin() 自己会去读；
    # 「未清理关注列表」落在标记文件上：是就删掉今天的标记让清理跑一遍，
    # 否就提前写上标记跳过清理。
    st = user.current()
    print(f"当前账号：{st['name']}（重新识别晶钻 {'是' if st['fresh'] else '否'}、"
          f"未清理列表 {'是' if st['purge'] else '否'}、"
          f"已手工吃 {st['manual']} 口）")
    if st["purge"]:
        purge_flag().unlink(missing_ok=True)
    else:
        mark_purged()

    dia = diamonds.Tracker("list")
    eaten_list, completed = run(total=args.total, dia=dia)
    eaten = eaten_list + st["manual"]

    if not completed:
        print("关注列表没跑完（自我重启失败，多半卡在什么界面上）。"
              "列表吃得比世界频道靠谱，不转世界频道。"
              "去模拟器里看一眼卡在哪，救回来重开就行")
        return
    if dia.full():
        core.print_blue(f"晶钻已经比开工时多出 {dia.gained()} 颗，"
                        f"今天的 {diamonds.DAILY_LIMIT} 口吃满了，不开世界频道")
        return
    if eaten >= args.target:
        print(f"关注列表里已经吃到 {eaten} 口，够 {args.target} 口了，收工")
        return
    if args.no_world:
        print(f"吃到 {eaten} 口，还差 {args.target - eaten} 口，按 --no-world 停下")
        return

    print(f"关注列表里吃到 {eaten} 口，还差 {args.target - eaten} 口，"
          f"转世界频道接着吃")
    core.ensure_world()

    # 直接在这条命令行里接着跑世界频道，不开面板：面板现在是个
    # 要自己选好配置再点「开始运行」的启动器（python core/panel.py），
    # 不再接受外部传参自动开工。这条命令行路是无界面的，
    # 晶钻满了自动收工，没有人可以问。计数器整个传过去，
    # 列表段最后一次真实读数也保住，不会拿扣过手工口数的起点
    # 去跟第一口比。
    import zuanfan_bot
    zuanfan_bot.main(eaten=eaten, eat_target=args.target, dia=dia)


if __name__ == "__main__":
    main()
