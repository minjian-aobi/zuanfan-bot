"""奥比岛：世界频道钻饭拜访抓虫脚本

流程：世界频道匹配钻饭
      → 同一行有粉丝字样则记下 → 点头像 → 按档位决定关注与去留
      → 点拜访 → 派对则先撒花 → 点三星气泡 → 点抓虫
      → 若画面上仍有三星则继续点 → 点公屏回世界频道 → 循环

用法：
    python core/panel.py                        带界面跑，滑块随时切档（推荐）
    python core/zuanfan_bot.py                  无界面跑，默认档（第二档）
    python core/zuanfan_bot.py --mode 2         无界面跑，指定档位，1 或 2
    python core/zuanfan_bot.py --quiet          只留每轮结果那一行，关掉过程提示
    python core/zuanfan_bot.py --no-diamonds    关掉晶钻计数，纯吃饭，没有 100 颗
                                                上限兜底（panel.py 后面也能带这个参数）
    python core/zuanfan_bot.py --debug --once   单步验证：打印每次匹配分数，跑一轮即退出

两档运行模式的定义在 modes.py，界面在 panel.py。这个文件只负责
把决策落到屏幕操作上，判定逻辑本身不写在这里。

基准分辨率 960x540。分辨率变化后所有模板图必须重裁，标定值也要重测。

三处固定坐标：公屏入口、抓虫按钮、头像横坐标。
这三个位置在界面上不动。
拜访按钮、三星气泡、撒花按钮、关注按钮的位置会变或依赖弹窗时机，
只能靠图像识别。

one_round 的返回码：
    ok          完整跑完一轮
    ok_fan      完整跑完一轮，进门前点了关注
    no_world    找不到世界频道
    no_target   当屏没有钻饭
    dup         这个头像最近去过
    skip_fan    第一档：同一行有粉丝字样，主动跳过。连资料卡都没开
    visited     第二档：资料卡上是取关按钮，说明已关注过，主动跳过
    no_card     点头像后没出现拜访按钮
    no_stars    超时没找到三星。无权限、加载太慢、当屏确实没三星都在这里

skip_fan 和 visited 是主动跳过，不是失败：它们省下的正是整轮十几秒。
看效率时要把它们和 no_target、no_stars 分开算。

每轮往 logs/run_YYYYMMDD.csv 追加一行。其中 mode 列是这一轮用的档位
（1 到 2），exit 列是家园内抓虫循环的退出原因，ate 列是这一家有没有吃到：
    clean  三星和撒花按钮都不在了，吃到了，ate=1
    max    抓满 STARS_MAX 次还看得见三星，说明一直没吃上，ate=0
    back   中途被弹回世界频道，无法确认，按没吃到算，ate=0
    blocked 撒花界面点不掉，三星可能一直被挡着，也按没吃到算，ate=0
每家每天只能吃一口，所以 ate 只有 0 和 1。抓虫点击次数 taps 不能当口数用，
同一家点六次也顶多吃到一口。

mode 是后加的列。加这一列之前就已经存在的当天 csv，表头里没有它，
新写的行会比表头多出一格。翻旧日志时注意这一点，新的一天不受影响。

主循环还会写两种只属于日志的行：
    error    这一轮抱了异常，多半是 adb 超时。明细列全空
    restart  连续找不到世界频道太久，关掉游戏重开了一次

每吃到一家还会往 logs/diamonds_YYYYMMDD.csv 写一行晶钻读数，
用来事后算「吃到了」这个判定的准确率。细节在 diamonds.py。

主循环可以带着一个已经吃到的口数开跑，也可以给一个总目标口数。
先跑关注列表再转世界频道时，follow_list.py 就是这么把两段接起来的：
列表里吃到的口数当起点传进来，凑满目标口数就停。晶钻起点也一起传进来，
两段共用同一个起点，当天的 100 颗上限才算得对。
"""

from __future__ import annotations

import sys
import time

import diamonds
import enter_wait
import follow_list
import modes
# 底层部件全部在 core.py：标定值、adb、模板匹配、去重、日志、单步动作。
# 用 import * 把它们搬到本模块名下，下面的代码写 screenshot() 而不是
# core.screenshot()，读起来跟拆分前一模一样；测试里桩掉
# zuanfan_bot.screenshot 也照样生效。
from core import *  # noqa: F401,F403

# 跳过这一家时，点公屏入口关掉资料卡最多点几次。判据是拜访字样还在不在：
# 它在就说明卡片没关掉，下一轮点头像会全部点空。一般一两下就行，
# 给到 5 是因为面板开关动画有时会吃掉一次点击。
CARD_CLOSE_MAX = 5

# 大重启按时间算，不按轮数。卡住的时候每轮要反复点遮挡、等超时，
# 一轮能拖到 30 秒，按轮数凑 20 轮实际要等十分钟。按时间算，
# 连续 60 秒没有一轮正常结果就是真卡住了。
STUCK_GAME_RESTART = 60.0   # 秒


def home_after_full() -> None:
    """吃满当天上限后回自己家，把游戏停在干净的地方再问人/收工。

    一轮收尾时 ensure_world 把公屏面板打开了，人其实还站在别人家里。
    面板开着时点右下角回家那两下会不会被面板吃掉没验证过，所以先
    点公屏入口把面板关掉再点，跟关注列表那条路（没有面板挡着，
    follow_list.run 里直接 restart_taps）对齐。
    人本来就在自己家时不能点那两下——这里一定是刚在别人家吃完，
    不存在这个情况。
    """
    if in_world(screenshot()):
        tap(*TAB_WORLD_PT)          # 公屏面板开着就先关掉
        time.sleep(WORLD_TAP_GAP)
    follow_list.restart_taps()


def close_card() -> bool:
    """点公屏入口，直到画面上看不到拜访字样。返回卡片是否真的关掉了。

    不能只点一下就交给 ensure_world：资料卡盖在公屏上时世界频道锚点往往
    还露着，ensure_world 会判定「已经在世界频道」直接返回，卡片就一直挂在
    那儿，下一轮点头像全点空。所以这里用拜访字样当判据，它只属于资料卡。

    每下之间隔 WORLD_TAP_GAP，理由跟 open_world 一样：面板加载要一秒多，
    点得比这快会把刚打开的面板又关掉。
    """
    for i in range(CARD_CLOSE_MAX):
        tap(*TAB_WORLD_PT)
        time.sleep(WORLD_TAP_GAP)
        score, visit = find(screenshot(), "btn_visit", VISIT_ROI)
        if not visit:
            return True
        if VERBOSE:
            print(f"  资料卡还在（拜访 {score:.3f}），再点第 {i + 2} 次")
    if VERBOSE:
        print(f"  点了 {CARD_CLOSE_MAX} 次资料卡还没关掉")
    return False


def one_round(dedup: Dedup, settings: modes.Settings | None = None,
              dia: diamonds.Tracker | None = None, eaten: int = 0):
    """跑一轮。返回 (返回码, 明细)。明细原样写进 csv，字段见 LOG_FIELDS。

    档位在开头读一次就固定下来，整轮沿用。中途重新读会出现
    「前半轮按档 1、后半轮按档 2」的混搭状态：比如按档 1 跳过了粉丝判断，
    又按档 2 去找取关按钮，行为无法解释，日志也对不上。

    dia 是晶钻计数器，eaten 是这一轮之前已经吃到的口数。两个都只服务
    于吃到之后那一次晶钻读数；不给 dia 就不读不写。
    """
    settings = settings or modes.Settings()
    mode = settings.mode

    detail: dict = {"skin": "", "fan": 0, "followed": 0, "taps": 0,
                    "exit": "", "ate": 0, "world_secs": "", "home_secs": "",
                    "mode": mode + 1}

    if not ensure_world():
        return "no_world", detail

    # 轮询等钻饭消息。以前是单帧没找到就直接回 no_target，实际上公屏消息
    # 是陆续刷上来的，多等几秒往往就有。
    #
    # 命中那一帧的截图要留着给后面算头像坐标和判粉丝用，不能重新截。
    #
    # 30 秒计时只在频道确实开着的时候才走：面板没打开的时间不算数。
    # 不这么干的话，面板一直打不开时计时照样到点，refresh_world
    # 又开关一轮，就成了「一直在打开、关上、重启」的死循环。
    t_world = None     # 频道确认开着、开始等钻饭的时刻；频道不在就是 None
    reopens = 0
    img = None
    hit = None
    noted = 0.0        # 上次报「等了多久」的时刻，控制刷屏频率

    while True:
        img = screenshot()

        if not in_world(img):
            # 先隔一拍再确认一次：锚点偶发单帧漏判，不能把开着的面板
            # 当成关掉的又给点一下（这个坐标是开关，点一下真会关）
            time.sleep(POLL)
            if in_world(screenshot()):
                continue
            t_world = None          # 频道不在，等钻饭的计时作废
            if reopens >= WORLD_REOPEN_MAX:
                # 这轮不磕了，算没找着钻饭（no_target）：下一轮开头的
                # ensure_world 会带着遮挡处理重新开，等于一直在接着试。
                # 绝不能报 no_world——那个会往大重启计数，面板一时点不开
                # 就重启游戏（两分钟）纯属浪费。面板真要死了，ensure_world
                # 同样开不开，连续 60 秒 no_world 自然会大重启，
                # 触发条件跟以前一模一样
                hit = None
                break
            reopens += 1
            if VERBOSE:
                print("  公屏锚点不在，点公屏入口，等面板出来再判")
            tap(*TAB_WORLD_PT)
            # 点完先等满几秒再看锚点：面板加载要一秒多，点完立刻看
            # 多半还没画出来，那时再点一下就把刚开的面板又关掉了
            wait_world_state(True)
            continue

        if t_world is None:
            t_world = time.time()   # 频道确认开着，从这里才开始计时
            noted = 0.0

        score, hit, which = find_any(img, "zuanfan", CHAT_ROI)
        if hit:
            break

        if time.time() - t_world >= TIMEOUT_TARGET:
            hit = None
            break

        # 每隔 NOTE_GAP 报一次还在等，让终端看得出脚本是活的。
        # 不每帧打印：一帧 TARGET_POLL 是 0.1 秒，会把屏幕刷满。
        waited = time.time() - t_world
        if VERBOSE and waited - noted >= NOTE_GAP:
            noted = waited
            print(f"  等钻饭 {waited:.0f}/{TIMEOUT_TARGET:.0f} 秒"
                  f"（当屏最高分 {score:.2f}）")

        time.sleep(TARGET_POLL)

    # 这个数是算供给量用的分母：在公屏上盯了多久才等到一条钻饭。
    # 只计频道开着的时间，中途面板掉了重开的不算。频道一直没开起来
    # 就放弃的那轮（t_world 是 None）没有计时可记，留空
    detail["world_secs"] = "" if t_world is None else round(time.time() - t_world, 1)

    if hit is None:
        # 公屏卡在一批旧消息上不动了：关掉重开换一批，下一轮再找。
        # 频道没开起来就放弃的那轮不刷新——那两下点在没开的面板上
        # 等于开关各一下，纯浪费还添乱
        if t_world is not None:
            refresh_world()
        return "no_target", detail

    detail["skin"] = which
    if VERBOSE:
        print(f"  命中 {which} {score:.3f} @ {hit}")

    # ── 时序敏感区域开始：命中后到点头像之间不得插入其他操作 ──

    h, w = img.shape[:2]
    ax = int(AVATAR_X_RATIO * w)
    ay = hit[1] + AVATAR_DY
    side = int(AVATAR_CROP * w)
    patch = img[max(0, ay - side // 2):ay + side // 2,
                max(0, ax - side // 2):ax + side // 2]
    if patch.size and dedup.check_and_add(dhash(patch)):
        refresh_world()
        return "dup", detail

    # 档 1 的粉丝判断必须提到点头像之前：判出来是粉丝就整轮不做了，
    # 连开资料卡的三秒都省掉。代价是往时序敏感区里挤进一次模板匹配，
    # 约两毫秒，相比省下的一整轮可以忽略。
    # 其他档的粉丝判断仍留在 tap 之后，一点耗时都不给这个区域添。
    fan = False
    fan_done = False
    if mode == modes.MODE_SKIP_FAN:
        fan = is_fan(img, hit)
        fan_done = True
        detail["fan"] = int(fan)
        if modes.skip_before_enter(mode, fan):
            if VERBOSE:
                print("  档 1：同一行有粉丝字样，这一家不吃")
            # 头像已经进了去重表，同一条消息不会被反复判定。
            # 公屏没被动过，也不用 ensure_world 收尾。
            return "skip_fan", detail

    tap(ax, ay)
    # ── 时序敏感区域结束：资料卡会持续显示，后续步骤可以从容 ──

    # 粉丝判断用的是点头像之前那张截图，公屏此刻已经被资料卡盖住了。
    # 档 2 完全靠关注按钮做决定，粉丝与否不影响行为，这次匹配可以省掉。
    if not fan_done and modes.needs_fan_check(mode):
        fan = is_fan(img, hit)
        detail["fan"] = int(fan)

    # 拜访按钮：在固定横坐标条带内灰度匹配，命中位置就是点击位置
    _, visit = wait_for("btn_visit", TIMEOUT_CARD, VISIT_ROI)
    if not visit:
        ensure_world()
        return "no_card", detail

    # 只在需要的时候才去卡片上找按钮：
    #   档 1 走到这里说明不是粉丝，关注功能完全不参与
    #   档 2 每次都要看，取关按钮就是「这家今天来过了」的标记
    action = modes.ACT_ENTER
    follow_pt = None
    if mode == modes.MODE_FOLLOW_MEMORY:
        follow_pt, unfollow_pt = card_buttons(mode)
        action = modes.card_action(mode, fan, follow_pt is not None,
                                  unfollow_pt is not None)

    if action == modes.ACT_SKIP:
        # 档 2：卡片上写着取关，说明已经关注过，也就是已经来吃过了。
        # 每家每天只有一口，再进去也是白跑十几秒。
        if VERBOSE:
            print("  第二档：已关注过，这家吃过了，不进门")
        # 点公屏坐标直到拜访字样消失，确认资料卡真的关了，详见 close_card。
        close_card()
        ensure_world()
        return "visited", detail

    followed = False
    if action == modes.ACT_FOLLOW_THEN_ENTER and follow_pt:
        tap(*follow_pt)
        time.sleep(FOLLOW_WAIT)
        followed = True
        detail["followed"] = 1
        if VERBOSE:
            print(f"  已点关注 @ {follow_pt}")
        # 按钮文字变了，资料卡布局可能跟着动，重新定位一次拜访按钮
        _, visit = wait_for("btn_visit", TIMEOUT_CARD, VISIT_ROI)
        if not visit:
            ensure_world()
            return "no_card", detail

    if VERBOSE:
        print(f"  点拜访 @ {visit}，等进门")
    tap(*visit)
    t_home = time.time()

    stars = enter_wait.enter_home()
    if not stars:
        detail["home_secs"] = round(time.time() - t_home, 1)
        ensure_world()
        return "no_stars", detail

    taps, why = catch_all_bugs(stars)
    detail["taps"] = taps
    detail["exit"] = why
    # 判据是退出时三星还在不在，不是抓了几次。每家每天只能吃一口，
    # 所以这里只有 0 和 1。详见 catch_all_bugs 的三种退出原因。
    detail["ate"] = 1 if why == "clean" else 0

    # 晶钻数只在主界面和家园里显示，回了世界频道就被公屏面板挡住
    # 读不到了。所以吃到后的读数必须抢在 ensure_world 之前，
    # 趁人还在别人家里。
    if detail["ate"] and dia is not None:
        dia.after_meal(eaten + 1)

    ensure_world()
    detail["home_secs"] = round(time.time() - t_home, 1)
    return ("ok_fan" if followed else "ok"), detail


def cli_mode() -> int:
    """从命令行读档位。--mode 后面接 1 或 2，就是界面上从左到右的档位。

    内部档位从 0 数，界面从 1 数，所以这里减一。给了看不懂的值就退回
    默认档并说一声，不抱异常：这个脚本要长时间无人看守地跑，
    能跑起来比严格校验重要。
    """
    if "--mode" not in sys.argv:
        return modes.MODE_START
    i = sys.argv.index("--mode")
    try:
        return modes.normalize(int(sys.argv[i + 1]) - 1)
    except (IndexError, ValueError):
        print("--mode 后面要接 1 或 2，这次按默认档跑")
        return modes.MODE_START


def main(settings: modes.Settings | None = None,
         stats: dict | None = None,
         eaten: int = 0,
         eat_target: int | None = None,
         dia_start: int | None = None,
         dia: diamonds.Tracker | None = None) -> None:
    """主循环。

    settings 和 stats 都是给 panel.py 传进来的：界面在主线程，这个函数
    在工作线程，两边只通过这两个对象通信。这个函数绍不碰任何界面控件，
    界面靠定时器自己来读 stats。反过来在线程里操作 tkinter 会偶发卡死。

    eaten 是开跑前已经吃到的口数。先跑关注列表再转世界频道时，
    列表里吃到的口数从这里传进来，界面上的数字和终端里的数字都接着往上加。

    eat_target 是一天总共要吃多少口。吃满就自己停下来，不给就一直跑。

    dia 是上一段已经在用的晶钻计数器（关注列表传下来的），直接给它比
    给 dia_start 强：它不光记着起点，还记着列表阶段最后一次真实读数，
    第一口的 delta 才能跟真实的上一笔比。dia_start 只是兜底——没给
    dia 时才拿它当起点新造一个计数器。

    直接命令行跑时这些参数都不给，自己造一份，行为跟以前一样。
    """
    has_ui = settings is not None and stats is not None
    settings = settings or modes.Settings(cli_mode())
    stats = stats if stats is not None else {}

    print("当前模式：", modes.mode_label(settings.mode))
    if eaten:
        print(f"已经吃到 {eaten} 口，接着往上数")
    if eat_target:
        print(f"吃满 {eat_target} 口就停")

    connect()
    dedup = Dedup(DEDUP_SIZE)

    if "--once" in sys.argv:              # 单步验证：跑一轮即退出
        result, detail = one_round(dedup, settings)
        log_row(result, detail)
        print(time.strftime("%H:%M:%S"), result, detail)
        return

    # 晶钻计数。只用来写日志和判当天的 100 颗上限，
    # 不拿它判单家该不该接着吃。读不出数时这一块相当于不存在。
    # 开关在 settings.count_diamonds 上：面板上有个勾选项，命令行
    # 加 --no-diamonds 也是拨它，跑的过程中随时能切。
    if "--no-diamonds" in sys.argv:
        settings.count_diamonds = False

    if not has_ui:
        # 命令行没有弹窗可用，开工读数失败时问人走控制台输入
        diamonds.ASK = diamonds.console_ask

    if dia is not None:
        # 列表段的计数器直接接过来用，换 source 让世界段的日志行对得上。
        # begin() 看到 start 已经有了就不会重读，last 里列表段最后一次
        # 真实读数也保住了，第一口的 delta 不会跟扣过手工口数的起点比
        dia.source = "world"
        dia.begin()
    elif settings.count_diamonds:
        dia = diamonds.Tracker("world", start=dia_start)
        dia.begin()
    else:
        print("晶钻计数已关，今天的 100 颗上限没人看着了")

    def dia_keys() -> dict:
        """晶钻三个显示键 + 计数器本体，给面板的晶钻栏用。

        dia_start/dia_now/dia_gain 是显示用：空串 = 没读到/没计数，
        面板上显示「未计数」。dia_obj 是计数器本体：面板暂停时改
        起点直接调它的 set_start（那时工作线程在空转，不碰它）。
        """
        return {"dia_obj": dia,
                "dia_start": "" if dia is None or dia.start is None else dia.start,
                "dia_now": "" if dia is None or dia.last is None else dia.last,
                "dia_gain": "" if dia is None or dia.gained() is None
                else dia.gained()}

    def handle_full() -> bool:
        """晶钻比起点多出 100 颗了怎么办。返回 True 收工，False 接着跑。

        有面板时把决定权交给用户：往 stats 里放 dia_full，面板的定时器
        看到就弹窗问「继续刷还是收工」，这里干等答案。用户选继续刷时
        面板会把 count_diamonds 拨掉，这边顺势把计数器扔了。
        没面板时（命令行直接跑）没人可问，照旧自动收工。

        回自己家不在这里做，在调用点做：这里分不清人是刚在别人家吃完
        （要点右下角两下回去）还是开跑前就已经满（人在主界面，不能点）。
        """
        nonlocal dia
        if not has_ui:
            print_blue(f"晶钻已经比开工时多出 {diamonds.DAILY_LIMIT} 颗，"
                       f"今天的 {diamonds.DAILY_LIMIT} 口吃满了，收工")
            return True

        stats["dia_full"] = (dia.start, dia.last)
        print_blue(f"晶钻已经比开工时多出 {dia.gained()} 颗，"
                   f"在面板上等决定：继续刷还是收工")
        while "dia_full" in stats and not settings.stopped:
            time.sleep(0.2)
        stats.pop("dia_full", None)       # 窗口被直接关掉时别留残键
        if settings.stopped:
            return True
        if not settings.count_diamonds:
            print("继续刷，晶钻计数关闭，100 颗上限不再生效")
            dia = None
            return False
        return True                       # 没等到明确答复，安全起见收工

    counts: dict[str, int] = {}
    rounds = 0                            # 跑完的轮数
    homes = 0                             # 进门成功的家数
    skipped = 0                           # 主动跳过的家数，不是失败
    stuck = 0                             # 连续多少轮没找到世界频道
    restarts = 0                          # 连续重启失败几次

    # 带着起点开跑时，界面不能等到第一轮跑完才显示数字，
    # 否则看上去像是把关注列表里吃到的那些口数丢了。
    # 晶钻键同理：开工识别到的起点要立刻上面板
    stats.update(rounds=rounds, homes=homes, eaten=eaten,
                 skipped=skipped, last="-", counts=counts, **dia_keys())

    if eat_target and eaten >= eat_target:
        print(f"开跑前就已经吃到 {eaten} 口，够 {eat_target} 口了，不开工")
        return

    if dia is not None and dia.full():
        print_blue(f"开跑前晶钻就已经比开工时多出 {dia.gained()} 颗")
        if handle_full():
            return

    pause_noted = False
    last_progress = time.time()    # 上一次拿到正常结果（不是 no_world）的时刻
    while not settings.stopped:
        if settings.paused:
            if not pause_noted:
                pause_noted = True
                # 进了空转才报 parked：这一刻起脚本真的不点屏幕了，
                # 面板看到 parked 才显示「已暂停」，之前是「暂停中…」
                settings.parked = True
                print("已暂停（这一轮跑完后停住），点「继续」恢复")
            time.sleep(IDLE_POLL)
            continue
        if pause_noted:
            pause_noted = False
            settings.parked = False

        # 面板上的计数开关随时能拨。拨开就现在建计数器——起点从当天
        # 日志里恢复，恢复不了才现场读屏；拨关就把计数器扔了。
        if settings.count_diamonds and dia is None:
            dia = diamonds.Tracker("world")
            dia.begin()
        elif not settings.count_diamonds and dia is not None:
            print("晶钻计数已关，100 颗上限不再生效")
            dia = None

        if VERBOSE:
            print(f"── 第 {rounds + 1} 轮　档位 {modes.MODE_NAMES[settings.mode]}"
                  f"　{time.strftime('%H:%M:%S')}")

        try:
            result, detail = one_round(dedup, settings, dia, eaten)
        except Exception as exc:          # 任何异常都不中断循环
            # adb 超时也走这里。记一行 error，否则日志上只剩下一段空白，
            # 事后分不清是脚本停了还是卡在 adb 里
            print("异常：", exc)
            log_row("error", {})
            time.sleep(2.0)
            continue

        log_row(result, detail)
        counts[result] = counts.get(result, 0) + 1
        rounds += 1
        if result in ("ok", "ok_fan"):
            homes += 1
        if result in ("skip_fan", "visited"):
            skipped += 1

        ate = int(detail["ate"])
        eaten += ate

        stuck = check_stuck(result, stuck)
        if result != "no_world":
            last_progress = time.time()
        elif (time.time() - last_progress > STUCK_GAME_RESTART
                and restarts < RESTART_MAX):
            # 连续一分钟每轮都是 no_world，不是一时加载慢。不管成败都把
            # 计时清零：成功了要从头算，失败了也要等下一个一分钟再试，
            # 否则每轮都会重启一次
            restarts = 0 if restart_game() else restarts + 1
            log_row("restart", {})
            last_progress = time.time()
            stuck = 0

        # 界面只读这一份，键名固定。写在打印之前，这样界面上看到的
        # 和终端里最后一行始终一致。晶钻键的含义见 dia_keys()。
        stats.update(rounds=rounds, homes=homes, eaten=eaten,
                     skipped=skipped, last=result, counts=counts,
                     **dia_keys())

        # 进门数和吃到数的差就是白跑的家：进了门、点了三星，但三星没消失。
        # 主动跳过的单独列，它省下的时间是收益，不能和白跑混在一起看
        print(time.strftime("%H:%M:%S"), result,
              f"进门 {homes} 吃到 {eaten} 跳过 {skipped}", counts)

        if dia is not None and dia.full():
            # 人刚在别人家吃完、公屏面板还开着。先回自己家再问人，
            # 不管答案是继续刷还是收工，游戏都停在干净的自己家里
            home_after_full()
            if handle_full():
                break

        if eat_target and eaten >= eat_target:
            print(f"吃到 {eaten} 口，够 {eat_target} 口了，收工")
            break

        time.sleep(0.3)

    # 暂停中点收工会从空转里直接退出循环，跳过了清 parked 的那一行，
    # 在这里兜底。不清的话面板的晶钻栏还以为线程停着，放行「修改」
    settings.parked = False


if __name__ == "__main__":
    main()

