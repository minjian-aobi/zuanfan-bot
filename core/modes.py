"""两档运行模式的定义与判定。

这里只放纯逻辑，不碰屏幕也不碰 adb，所以可以脱离模拟器单独测。
真正去找按钮、点按钮的部分在 zuanfan_bot.py 里，这里只回答
“看到这些东西之后应该怎么办”。

两档从左到右：
    0 无限制        只吃无限制钻饭。公屏上钻饭同一行有粉丝字样就不吃，
                     直接换下一条
    1 全部关注查重    两种钻饭都吃，而且每家都点关注，
                     拿游戏内的关注状态当“这家吃过了”的标记：
                     卡片上是关注按钮说明没吃过，关注完再进去吃；
                     是取关按钮说明已经吃过，直接走，省下这一趟的时间。
                     这是默认档

档位可以在跑的过程中随时改，所以用 Settings 包一层锁：tkinter 界面在
主线程写，bot 在工作线程读。档位只在每轮开头读一次并整轮沿用，
不在一轮中间重新读，否则拖进度条的瞬间可能出现“前半轮按第一档、
后半轮按第二档”的混搭状态。

内部档位从 0 数，MODE_NAMES 里也不带编号：界面上不显示任何档位数字，
只有命令行参数 --mode 和终端打印用 1 到 2 的编号。
"""

from __future__ import annotations

import threading

MODE_SKIP_FAN = 0
MODE_FOLLOW_MEMORY = 1
MODE_COUNT = 2

# 不指定档位时用哪一档。界面开起来是它，--mode 给了看不懂的值也退到它。
MODE_START = MODE_FOLLOW_MEMORY

MODE_NAMES = ("无限制", "全部关注查重")

MODE_DESCS = (
    "只吃无限制钻饭",
    "吃无限制钻饭＋粉丝钻饭，全部点关注从而避免重复进入同一家",
)

# card_action 的三种返回值
ACT_ENTER = "enter"                  # 直接点拜访进门
ACT_FOLLOW_THEN_ENTER = "follow"     # 先点关注，再点拜访进门
ACT_SKIP = "skip"                    # 不进门，直接回世界频道


class Settings:
    """界面与 bot 共用的一小块可变状态。所有读写都过锁。

    锁的必要性：tkinter 必须待在主线程，bot 只能放工作线程，
    两边同时碰 mode 和 paused。Python 的 GIL 保不住读写组合的原子性，
    而这两个值直接影响行为，不能出现读到一半的情况。
    """

    def __init__(self, mode: int = MODE_START) -> None:
        self._lock = threading.Lock()
        self._mode = normalize(mode)
        self._paused = False
        self._stopped = False
        # 「真的停住了」回执。paused 只是面板发来的暂停请求，工作线程
        # 要把当前这家跑完、进入空转等待时才把 parked 置真，离开空转
        # 时置假。面板只有看到 parked 才显示「已暂停」：请求刚发出去
        # 那几秒脚本其实还在点，这时候说「已暂停」的话，用户去模拟器
        # 上动手，脚本再点一下坐标就全乱了。
        self._parked = False
        # 晶钻计数开关，默认开。面板上的勾选项和 --no-diamonds 都改它。
        # 关掉之后 100 颗的当天硬上限就没人看着了。
        self._count_diamonds = True

    @property
    def count_diamonds(self) -> bool:
        with self._lock:
            return self._count_diamonds

    @count_diamonds.setter
    def count_diamonds(self, value: bool) -> None:
        with self._lock:
            self._count_diamonds = bool(value)

    @property
    def mode(self) -> int:
        with self._lock:
            return self._mode

    @mode.setter
    def mode(self, value: int) -> None:
        with self._lock:
            self._mode = normalize(value)

    @property
    def paused(self) -> bool:
        with self._lock:
            return self._paused

    @paused.setter
    def paused(self, value: bool) -> None:
        with self._lock:
            self._paused = bool(value)

    @property
    def parked(self) -> bool:
        with self._lock:
            return self._parked

    @parked.setter
    def parked(self, value: bool) -> None:
        with self._lock:
            self._parked = bool(value)

    @property
    def stopped(self) -> bool:
        with self._lock:
            return self._stopped

    def stop(self) -> None:
        with self._lock:
            self._stopped = True


def normalize(mode: int) -> int:
    """把任意输入收到合法档位。越界或不是整数都退到 MODE_START。

    界面上的进度条不会给出非法值，但命令行参数会（比如 --mode 7）。
    退到 MODE_START 而不是报错，是因为这个脚本要长时间无人看守地跑，
    能跑比跑得完全符合预期重要。
    """
    try:
        mode = int(mode)
    except (TypeError, ValueError):
        return MODE_START
    if mode < 0 or mode >= MODE_COUNT:
        return MODE_START
    return mode


def mode_label(mode: int) -> str:
    """终端打印用的一行描述，带 1 到 2 的编号。

    界面不用这个，界面直接读 MODE_NAMES 和 MODE_DESCS，不显示编号。
    终端里保留编号是因为 --mode 参数用的就是这个编号，对得上。
    """
    mode = normalize(mode)
    return f"{mode + 1} {MODE_NAMES[mode]}：{MODE_DESCS[mode]}"


def skip_before_enter(mode: int, fan_row: bool) -> bool:
    """还没点头像就能定下来的跳过。

    只有第一档用得上。粉丝判断靠的是公屏截图，不需要开资料卡，
    所以这一档能在点头像之前就抵掉整轮，连开卡的三秒都省了。
    """
    return normalize(mode) == MODE_SKIP_FAN and bool(fan_row)


def card_action(mode: int, fan_row: bool, has_follow: bool,
                has_unfollow: bool) -> str:
    """资料卡已经开着，决定接下来干什么。

    参数里的 has_follow / has_unfollow 是“卡片上能不能匹配到这两个按钮”。
    两者互斥：同一个位置上要么写关注要么写取关。但不能假设总有一个命中：
    加载未完、模板偏差、自己的卡片都会导致两个都不命中。

    第二档两个都没命中时选择进门而不是跳过。两类错的代价不对称：
    误跳过是白白丢掉一家，误进门顶多白跑十几秒。模板失配时宁愿多跑。
    """
    mode = normalize(mode)

    if mode == MODE_FOLLOW_MEMORY:
        if has_unfollow:
            return ACT_SKIP
        if has_follow:
            return ACT_FOLLOW_THEN_ENTER
        return ACT_ENTER

    # 第一档能走到这里说明已经确认不是粉丝，关注功能完全不参与
    return ACT_ENTER


def needs_unfollow_check(mode: int) -> bool:
    """这一档要不要在卡片上多找一次取关按钮。

    只有第二档需要。其他档位跳过这次匹配，省下一次模板匹配的开销。
    """
    return normalize(mode) == MODE_FOLLOW_MEMORY


def needs_fan_check(mode: int) -> bool:
    """这一档要不要判粉丝。

    第二档完全靠关注按钮做决定，粉丝不粉丝不影响行为，不必浪费这次匹配。
    但仍旧把结果记进日志会有分析价值，所以这里只管“行为需不需要”。
    """
    return normalize(mode) == MODE_SKIP_FAN

