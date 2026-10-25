"""控制面板：先选账号和流程，点「开始运行」才开工，跑着的时候实时看状态。

用法：
    python core/panel.py            开面板。打开后什么都不跑，选好信息点开始
    python core/panel.py --debug    同上，额外打印匹配分数

开工配置上半部分是单列，从上到下的联动规矩：不勾选就整条变灰。
    账号                下拉选已有账号，也可以直接打字建新号
    晶钻计数            满 __ 颗时问我。默认一直是 100，就是当天硬上限：
                        起点会从基线/日志恢复，「现在 − 起点」就是今天
                        累计吃的颗数，不用再减。只有今天开跑前自己手工
                        吃过、而且今天还没跑过脚本（没有基线可恢复）时，
                        才填 100 减去手工吃的颗数
                        勾掉它，「新的一天」就一起变灰：起点都没了，
                        重不重置没意义
    今天是新的一天      勾上 = 重新设置晶钻起点（仅新号/今天没记录时生效；
                        有记录的号一律沿用原起点）

下半部分左右两块浅蓝面板，各自整体可勾选，勾掉整列变灰：
    左：先吃关注列表    下面挂「今天还未清理关注列表」
    右：后吃世界频道    下面挂两档运行模式条（金色那段是选中的档）
    两个都勾掉时「开始运行」锁死——不跑任何流程开什么工。

「晶钻计数」跑着的时候也能拨，每轮开头生效。计数开着、晶钻比开工时
多出设定颗数时，bot 先点右下角两下回自己家，再停下来等弹窗：
继续刷（顺便关掉计数）还是收工。关注列表和世界频道两段都会这样问。

控制卡上的晶钻栏显示开工时识别到的初始晶钻数和当前读数。
初始数在「已暂停」时可以改：改完写一笔 source=manual 的晶钻日志，
并更新当天的基线文件（不改基线的话，同一天重开脚本修正就丢了）。

暂停是两个状态，别混：「暂停中…」是请求刚收到、当前这家还没跑完，
脚本还在点屏幕；「已暂停」是工作线程真的停稳了（和控制台那行
「已暂停」是同一时刻）。只有真的停稳，改晶钻数的按钮才可点，
这时去模拟器上手动操作也是安全的。

从 start.bat 启动时那个黑窗口别关：关注人数 OCR 失败时要在那里
手输人数，过程的详细打印也都在那里。

不想要界面就照旧跑 python core/follow_list.py 或 python core/zuanfan_bot.py，
命令行那条路完全没变。

标题为什么画在画布里：用 grid 摆几个 Label 时，列宽由文字长度决定，
“无限制”三个字和“全部关注查重”六个字占的宽度差一倍，标题永远对不到
进度条各段的中心上。画在同一张画布上就能直接指定像素坐标，
每个标题的中心就是对应那一段的中心，不受字数影响。

为什么是 tkinter：Python 自带，不用装任何东西，也不需要起本地服务端。
界面和 bot 在同一个进程里，点进度条就是改同一个 Settings 对象，
下一轮立刻生效。

线程分工必须是这个方向：tkinter 的事件循环只能在主线程跑，
所以 bot 去工作线程。反过来把 tkinter 丢进线程在 Windows 上会直接崩。
工作线程设为 daemon，关窗口时不会残留一个杀不掉的后台进程。
工作线程绝不碰 tkinter 控件，只往 Settings 和 stats 里写，
界面靠定时器自己来读。这是 tkinter 的硬规矩，违反会偶发卡死。

一个教训：tkinter 的 pack/grid 只认「创建时指定的父容器」，
把 A 容器建出来的控件 pack 进 B 容器不会报错，但控件会出现在
A 里，布局看起来就是散的。所有同排显示的控件必须同一个父。
"""

from __future__ import annotations

import ctypes
import threading
import time
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

import core
import diamonds
import follow_list
import modes
import user
import zuanfan_bot as z

REFRESH_MS = 500          # 界面刷状态的间隔

WIN_TITLE = "钻饭脚本控制面板"

# 配色：白底为主，蓝色是主色（选中、在跑、主按钮），金色给运行模式条
# 和收工按钮，两块流程面板用浅蓝底（蓝色 12% 兑白的预混色，tkinter
# 没有真半透明），灰色给禁用/停止。
C_BG = "#f5f6f8"
C_CARD = "#ffffff"
C_CARD_EDGE = "#e4e7ec"
C_TEXT = "#1d2939"
C_SUB = "#667085"
C_BLUE = "#24AEEF"
C_BLUE_DARK = "#0f8fd0"
C_BLUE_TINT = "#e3f4fd"      # 两块流程面板的底色
C_GOLD = "#F6D2A6"
C_GOLD_DARK = "#b07400"
C_WARN = "#ef6c00"
C_DEAD = "#98a2b3"
C_HINT = "#b3b9c2"           # 输入框里的灰色提示字
C_OFF = "#d8dde3"            # 未选中的段/禁用底

BAR_W = 200               # 档位条总宽（96 DPI 基准），它在右列面板里
BAR_H = 10
LABEL_H = 22              # 标题画布的高度

F_TITLE = ("Microsoft YaHei UI", 13, "bold")
F_H2 = ("Microsoft YaHei UI", 10, "bold")
F_BODY = ("Microsoft YaHei UI", 10)
F_SUB = ("Microsoft YaHei UI", 9)
F_ON = ("Microsoft YaHei UI", 9, "bold")
F_OFF = ("Microsoft YaHei UI", 9)
# 配置卡里的勾选/单选用大号字。经典 tk 控件的选项框跟着字号走，
# 字大框才大，小字号的框在高分屏上小得看不清
F_PICK = ("Microsoft YaHei UI", 12)

# 高 DPI 缩放倍数，main 里在建窗口之后按屏幕实际 DPI 改写。
# 字体按磅数走会自动跟着系统缩放变大，画布和留白是按像素写的，
# 得手动乘同一个倍数，不然字大了条子还是那么小。
SCALE = 1.0


def px(v: float) -> int:
    """把 96 DPI 下的像素数换算成当前屏幕的实际像素数。"""
    return int(round(v * SCALE))


def card(parent: tk.Misc, bg: str = C_CARD) -> tk.Frame:
    """白底（或指定底色）细边的卡片容器，所有内容块都装在卡片里。"""
    return tk.Frame(parent, bg=bg, highlightbackground=C_CARD_EDGE,
                    highlightthickness=1, bd=0)


def run_flow(settings: modes.Settings, stats: dict,
             do_list: bool, do_world: bool, target: int | None) -> None:
    """工作线程本体：按面板上勾的流程跑。

    全程只写 settings 和 stats，不碰任何界面控件（tkinter 的硬规矩）。
    关注列表阶段的进度都打在控制台里，列表跑完转世界频道后
    世界频道的统计才开始走。
    """
    st = user.current()
    print(f"当前账号：{st['name']}（未清理列表 {'是' if st['purge'] else '否'}）")

    # 点开始运行时游戏没在跑就先拉起：沿用大重启那套启动逻辑
    #（monkey 拉起、点两下进游戏、等加载页），再开世界频道。
    # 游戏本来就在跑时这步什么都不做，不影响手动开好游戏再点开始的老用法。
    stats.update(last="检查游戏是否在运行…")
    if not core.start_game():
        print("游戏启动失败，这次开工取消。手动把游戏开起来再点开始")
        stats.update(last="游戏没起来，收工")
        return

    # 「未清理关注列表」落在标记文件上：是就删掉今天的标记让清理跑一遍，
    # 否就提前写上标记跳过清理。清理本身还是 daily_purge 那套。
    if st["purge"]:
        follow_list.purge_flag().unlink(missing_ok=True)
    else:
        follow_list.mark_purged()

    dia = diamonds.Tracker("list", enabled=settings.count_diamonds)
    # 计数器本体放进 stats：面板的晶钻栏读它显示初始晶钻数，
    # 暂停时改起点也是直接调它的 set_start（真的暂停时工作线程
    # 在空转，不碰这个对象，所以不会打架）
    stats["dia_obj"] = dia
    eaten = 0

    if do_list:
        while True:
            stats.update(last="关注列表巡回中…")
            eaten_list, completed = follow_list.run(
                total=None, dia=dia,
                should_stop=lambda: settings.stopped,
                paused=lambda: settings.paused,
                parked=lambda v: setattr(settings, "parked", v),
                stats=stats)
            eaten += eaten_list
            stats.update(eaten=eaten)

            if settings.stopped:
                return
            if not completed:
                print("关注列表没跑完（自我重启失败，多半卡在什么界面上）。"
                      "列表吃得比世界频道靠谱，不转世界频道。"
                      "去模拟器里看一眼卡在哪，救回来重开就行")
                stats.update(last="列表中断，收工")
                return
            if not dia.full():
                break

            # 晶钻满了。run 里已经点右下角两下回过家了，这里只负责问人，
            # 跟世界频道那段共用 dia_full 这个弹窗口子：继续刷 = 面板把
            # 计数开关拨掉，收工 = settings.stop()
            core.print_blue(f"晶钻已经比开工时多出 {dia.gained()} 颗，"
                            f"到设定的上限了，在面板上等决定：继续刷还是收工")
            stats["dia_full"] = (dia.start, dia.last)
            while "dia_full" in stats and not settings.stopped:
                time.sleep(0.2)
            stats.pop("dia_full", None)   # 窗口被直接关掉时别留残键
            if settings.stopped:
                stats.update(last="晶钻已满，收工")
                return
            dia.enabled = False     # 面板已把计数开关拨掉，计数器跟上
            if do_world:
                break
            print("继续刷：再吃一遍关注列表")

        if not do_world:
            print(f"只跑关注列表：吃到 {eaten} 口，收工")
            stats.update(last="列表完成，收工")
            return
        if target and eaten >= target:
            print(f"已经点到 {eaten} 个气泡，够 {target} 个了，不进世界频道")
            stats.update(last="已达标，收工")
            return
        print(f"关注列表吃到 {eaten} 口，转世界频道接着吃")
        stats.update(last="转世界频道…")
        core.ensure_world()

    if do_world:
        z.main(settings=settings, stats=stats, eaten=eaten,
               eat_target=target,
               dia=dia if dia.enabled else None)


def build(root: tk.Tk, settings: modes.Settings, stats: dict) -> None:
    root.title(WIN_TITLE)
    root.resizable(False, False)
    root.configure(bg=C_BG)

    # 条子的像素尺寸跟缩放倍数走。seg_w 先取整再反推总宽，
    # 保证各段严丝合缝，不会出现最后一截差一个像素
    n_modes = modes.MODE_COUNT
    seg_w = px(BAR_W) // n_modes
    bar_w = seg_w * n_modes
    bar_h = max(6, px(BAR_H))
    label_h = px(LABEL_H)

    outer = tk.Frame(root, bg=C_BG)
    outer.pack(padx=px(16), pady=px(14))

    # ── 头部：标题 + 状态 ──
    head = tk.Frame(outer, bg=C_BG)
    head.pack(fill="x", pady=(0, px(10)))
    tk.Label(head, text="钻饭控制面板", font=F_TITLE, fg=C_TEXT,
             bg=C_BG).pack(side="left")
    state_label = tk.Label(head, text="● 待启动", font=F_BODY,
                           fg=C_DEAD, bg=C_BG)
    state_label.pack(side="right", pady=(px(4), 0))

    # ── 开工配置卡。点开始之前随便改，开始后整卡锁死（晶钻计数除外） ──
    c0 = card(outer)
    c0.pack(fill="x", pady=(0, px(10)))
    inner0 = tk.Frame(c0, bg=C_CARD)
    inner0.pack(padx=px(14), pady=px(12), fill="x")
    tk.Label(inner0, text="开工配置", font=F_H2, fg=C_GOLD_DARK,
             bg=C_CARD).pack(anchor="w", pady=(0, px(6)))

    st0 = user.current()
    data0 = user.load()
    names = [n for n in data0.get("accounts", {}) if n != "default"]

    acc_var = tk.StringVar(value="" if st0["name"] == "default"
                           else st0["name"])
    fresh_var = tk.BooleanVar(value=st0["fresh"])
    purge_var = tk.BooleanVar(value=st0["purge"])
    target_var = tk.StringVar(value=str(follow_list.EAT_TARGET))
    list_var = tk.BooleanVar(value=True)
    world_var = tk.BooleanVar(value=True)
    dia_var = tk.BooleanVar(value=settings.count_diamonds)
    limit_var = tk.StringVar()
    limit_auto = {"on": True}      # False = 用户动过手，不再自动改
    touched = {"fresh": False, "purge": False, "list": False}

    def compute_defaults(*_args) -> None:
        """按账号的当天记录，给「今天」框、流程勾选和上限算默认值。

        都能从今天留下的文件推出来，不用人记：
            清过列表没    purge_今天_账号.flag 在 = 清过了。
                        清过说明今天列表已经跑过一轮，剩下的多半是
                        没饭的，所以「先吃关注列表」默认也取消，
                        直接去世界频道
            新的一天      基线文件不在 = 今天还没跑过，默认勾上
            还能吃几颗    晶钻日志里 gained 的最大值，100 减它
        人一旦手动勾过/改过，换账号时就不再覆盖——人的选择优先。
        """
        name = acc_var.get().strip() or "default"
        day = core.game_day()

        flag = follow_list.PURGE_DIR / f"purge_{day}_{name}.flag"
        purged = flag.exists()
        if not touched["purge"]:
            purge_var.set(not purged)
        if not touched["list"]:
            list_var.set(not purged)

        if not touched["fresh"]:
            baseline = (diamonds.LOG_DIR /
                        f"diamonds_start_{day}_{name}.txt")
            fresh_var.set(not baseline.exists())

        if limit_auto["on"]:
            # 上限默认永远是 100，就是当天的硬上限，不读日志减已吃。
            # 起点会从基线/日志恢复（begin 里的 recover_today），
            # 「现在 − 起点」本身就等于今天累计吃的颗数；这里再减一遍
            # 已吃就等于减了两次，反而吃不到 100。不读全局的
            # DAILY_LIMIT——它可能还停在上一个号填的数上。
            limit_var.set(str(diamonds.GAME_DAILY_MAX))
            diamonds.DAILY_LIMIT = diamonds.GAME_DAILY_MAX

    def on_limit_write(*_args) -> None:
        """框里的数就是上限，不管是自动填的还是人打的，都同步给
        diamonds.DAILY_LIMIT。区别只在字色：自动填灰字、人打黑字，
        人一旦动过手就不再自动改。"""
        limit_entry.config(fg=C_HINT if limit_auto["on"] else C_TEXT)
        raw = limit_var.get().strip()
        try:
            n = int(raw)
            if n <= 0:
                raise ValueError
            diamonds.DAILY_LIMIT = n
        except ValueError:
            if raw:
                diamonds.DAILY_LIMIT = 100

    def on_limit_key(_event) -> None:
        """在框里按键就算用户动过手。trace 分不清人和程序，按键能。"""
        limit_auto["on"] = False

    limit_var.trace_add("write", on_limit_write)
    acc_var.trace_add("write", compute_defaults)

    def pick(parent, text, var, command=None) -> tk.Checkbutton:
        """配置卡里的勾选框，统一字号和禁用灰。disabledforeground 必须
        单独给：tk 的禁用态不看 fg，不看的话变灰永远是说说而已。"""
        return tk.Checkbutton(
            parent, text=text, variable=var, command=command,
            font=F_PICK, fg=C_TEXT, bg=parent["bg"],
            activebackground=parent["bg"], selectcolor=C_CARD,
            disabledforeground=C_DEAD, anchor="w", justify="left")

    # 第 1 行：账号
    row = tk.Frame(inner0, bg=C_CARD)
    row.pack(fill="x", pady=px(2))
    tk.Label(row, text="账号", font=F_SUB, fg=C_SUB, bg=C_CARD,
             width=8, anchor="w").pack(side="left")
    acc_box = ttk.Combobox(row, textvariable=acc_var, values=names,
                           width=18, font=F_BODY)
    acc_box.pack(side="left")
    tk.Label(row, text="（可打字建新号）", font=F_SUB, fg=C_DEAD,
             bg=C_CARD).pack(side="left", padx=(px(6), 0))

    # 第 2 行：晶钻计数（满 __ 颗时问我）。
    # 整行三个控件必须同一个父容器，否则 pack 会各回各家（见模块说明）
    dia_row = tk.Frame(inner0, bg=C_CARD)
    dia_row.pack(fill="x", pady=(px(6), px(2)))
    dia_cb = pick(dia_row, "晶钻计数（满", dia_var)
    dia_cb.pack(side="left")
    limit_entry = tk.Entry(dia_row, textvariable=limit_var, width=5,
                           font=F_BODY, justify="center",
                           disabledbackground="#f2f4f7",
                           disabledforeground=C_DEAD)
    limit_entry.pack(side="left")
    dia_tail = tk.Label(dia_row, text="颗时问我）", font=F_PICK, fg=C_TEXT,
                        bg=C_CARD)
    dia_tail.pack(side="left")

    # 第 3 行：今天是新的一天。它不只是「新的一天」，也是「重置基线」
    # 的手段：勾上 = 无视今天的基线重读。所以平时不用勾，只有
    # 吃饭以外的途径让晶钻变了、想重对基线时才勾。
    # 默认值是按基线文件推的，人勾过就不再自动改
    fresh_cb = pick(inner0, "今天是新的一天（重新设置晶钻起始点）", fresh_var,
                    command=lambda: touched.__setitem__("fresh", True))
    fresh_cb.pack(fill="x", pady=px(2))

    # 第 4 行：最多尝试点击 __ 个钻饭气泡。控件照建（下面开工、变灰都要用到），
    # 但不 pack，面板上不显示，值固定是 follow_list.EAT_TARGET
    row = tk.Frame(inner0, bg=C_CARD)
    tk.Label(row, text="最多尝试点击", font=F_SUB, fg=C_SUB, bg=C_CARD,
             width=10, anchor="w").pack(side="left")
    target_entry = tk.Entry(row, textvariable=target_var, width=6,
                            font=F_BODY, justify="center",
                            disabledbackground="#f2f4f7",
                            disabledforeground=C_DEAD)
    target_entry.pack(side="left")
    tk.Label(row, text="个钻饭气泡，防止吃撑", font=F_SUB, fg=C_SUB,
             bg=C_CARD).pack(side="left")

    def on_dia_toggle() -> None:
        # 拨开关就是改 Settings，bot 每轮开头看一眼，立刻生效。
        # 勾掉时「新的一天」和颗数框整行变灰：起点都没了，重不重置没意义
        settings.count_diamonds = dia_var.get()
        on = "normal" if dia_var.get() else "disabled"
        fresh_cb.config(state=on)
        limit_entry.config(state=on)
        dia_tail.config(fg=C_TEXT if dia_var.get() else C_DEAD)

    dia_cb.config(command=on_dia_toggle)
    limit_entry.bind("<Key>", on_limit_key)

    # 初始填一次默认值（两个「今天」框 + 灰字上限），
    # 晚于相关控件建好之后
    compute_defaults()

    # ── 下半部分：左右两块浅蓝面板，左 1/3 右 2/3。
    # 用 grid 的 weight 分宽：pack 的 expand 只能均分余量，给不出 1:2。
    cols = tk.Frame(inner0, bg=C_CARD)
    cols.pack(fill="x", pady=(px(10), 0))
    cols.columnconfigure(0, weight=1, uniform="flow")
    cols.columnconfigure(1, weight=1, uniform="flow")

    left_panel = card(cols, bg=C_BLUE_TINT)
    left_panel.grid(row=0, column=0, sticky="nsew")
    lp = tk.Frame(left_panel, bg=C_BLUE_TINT)
    lp.pack(padx=px(12), pady=px(10), anchor="w")

    right_panel = card(cols, bg=C_BLUE_TINT)
    right_panel.grid(row=0, column=1, sticky="nsew", padx=(px(10), 0))
    rp = tk.Frame(right_panel, bg=C_BLUE_TINT)
    rp.pack(padx=px(12), pady=px(10), anchor="w")

    list_cb = pick(lp, "先吃关注列表", list_var)
    list_cb.pack(anchor="w")
    # 清理勾选是主流程的子项，用小一号的字压低存在感。
    # 默认值是按今天的清理标记推的，人勾过就不再自动改
    purge_cb = pick(lp, "今天还未清理关注列表\n（取关所有没饭的人）",
                    purge_var,
                    command=lambda: touched.__setitem__("purge", True))
    purge_cb.config(font=F_BODY)
    purge_cb.pack(anchor="w", padx=(px(26), 0), pady=(px(4), 0))

    world_cb = pick(rp, "后吃世界频道", world_var)
    world_cb.pack(anchor="w")
    mode_lbl = tk.Label(rp, text="运行模式", font=F_H2, fg=C_TEXT,
                        bg=C_BLUE_TINT)
    mode_lbl.pack(anchor="w", pady=(px(8), 0))

    bar = tk.Canvas(rp, width=bar_w, height=bar_h,
                    highlightthickness=1, highlightbackground=C_CARD_EDGE,
                    cursor="hand2", bg=C_BLUE_TINT)
    bar.pack(anchor="w", pady=(px(8), 0))
    tags = tk.Canvas(rp, width=bar_w, height=label_h,
                     highlightthickness=0, bg=C_BLUE_TINT)
    tags.pack(anchor="w", pady=(px(2), 0))
    desc = tk.Label(rp, text="", wraplength=bar_w, justify="left",
                    font=F_SUB, fg=C_SUB, bg=C_BLUE_TINT)
    desc.pack(anchor="w", pady=(px(4), 0))

    def draw(mode: int) -> None:
        """重画进度条和各档标题。选中第 n 档就把前 n+1 段填金。

        世界频道没勾选时整条变灰：那部分本来就不参与，不该还
        金着吸引注意力。
        """
        enabled = world_var.get()
        color_on = C_GOLD if enabled else C_OFF
        text_on = C_GOLD_DARK if enabled else C_DEAD
        bar.delete("all")
        tags.delete("all")
        for i in range(n_modes):
            bar.create_rectangle(
                i * seg_w, 0, (i + 1) * seg_w, bar_h,
                fill=color_on if i <= mode else C_OFF,
                outline="")
            on = i == mode
            tags.create_text(
                i * seg_w + seg_w // 2, label_h // 2,
                text=modes.MODE_NAMES[i], anchor="center",
                font=F_ON if on else F_OFF,
                fill=text_on if on else C_DEAD)

    def select(mode: int) -> None:
        settings.mode = mode
        draw(settings.mode)
        desc.config(text=modes.MODE_DESCS[settings.mode])

    def on_flow_toggle() -> None:
        """左右两个勾选控制各自面板的可用性：勾掉就整块变灰。

        两个都勾掉时开始按钮也锁死——不跑任何流程开什么工。
        """
        purge_cb.config(state="normal" if list_var.get() else "disabled")

        on_w = world_var.get()
        mode_lbl.config(fg=C_TEXT if on_w else C_DEAD)
        desc.config(fg=C_SUB if on_w else C_DEAD)
        draw(settings.mode)
        start_btn.config(
            state="normal" if (list_var.get() or on_w) else "disabled")

    list_cb.config(command=lambda: (touched.__setitem__("list", True),
                                    on_flow_toggle()))
    world_cb.config(command=on_flow_toggle)

    def on_click(event) -> None:
        """按下或拖动时按横坐标落在哪一段来定档位。

        坐标要夹回 0 到最后一档：拖到条子外面时 event.x 会是负数或超过总宽，
        不夹的话会算出不存在的档。settings.mode 的 setter 虽然也会兜住越界值，
        但它兜不住的结果是退回默认档，那就成了“往右拖反而跳到另一档”。
        """
        if not world_var.get():
            return
        seg = int(event.x // seg_w)
        select(max(0, min(n_modes - 1, seg)))

    for widget in (bar, tags):            # 点标题也算选那一档，点击区更好命中
        widget.bind("<Button-1>", on_click)
        widget.bind("<B1-Motion>", on_click)

    start_btn = tk.Button(inner0, text="开始运行", font=F_H2,
                          relief="flat", bg=C_BLUE, fg="#ffffff",
                          activebackground=C_BLUE_DARK,
                          activeforeground="#ffffff", cursor="hand2")
    start_btn.pack(fill="x", pady=(px(12), 0))

    # ── 控制卡 ──
    c3 = card(outer)
    c3.pack(fill="x")
    inner3 = tk.Frame(c3, bg=C_CARD)
    inner3.pack(padx=px(14), pady=px(12), fill="x")

    # 晶钻栏：初始晶钻数是开工时识别到的起点，跑着的时候只读；
    # 真的暂停了（parked）框和「修改」才可点。禁用态的字色用正文字色
    # 而不是灰色：这个框首先是拿来看数的，灰字看不清
    dia_bar = tk.Frame(inner3, bg=C_CARD)
    dia_bar.pack(fill="x", pady=(0, px(10)))
    tk.Label(dia_bar, text="初始晶钻", font=F_SUB, fg=C_SUB,
             bg=C_CARD).pack(side="left")
    dia_start_var = tk.StringVar()
    dia_start_entry = tk.Entry(dia_bar, textvariable=dia_start_var,
                               width=8, font=F_BODY, justify="center",
                               state="disabled",
                               disabledbackground="#f2f4f7",
                               disabledforeground=C_TEXT)
    dia_start_entry.pack(side="left", padx=(px(4), px(2)))
    tk.Label(dia_bar, text="颗", font=F_SUB, fg=C_SUB,
             bg=C_CARD).pack(side="left")
    dia_edit_btn = tk.Button(dia_bar, text="修改", font=F_SUB,
                             relief="flat", bg=C_BLUE, fg="#ffffff",
                             activebackground=C_BLUE_DARK,
                             activeforeground="#ffffff", cursor="hand2",
                             state="disabled")
    dia_edit_btn.pack(side="left", padx=(px(8), 0))
    tk.Label(dia_bar, text="（暂停后可改）", font=F_SUB, fg=C_DEAD,
             bg=C_CARD).pack(side="left", padx=(px(4), 0))
    dia_now_label = tk.Label(dia_bar, text="", font=F_SUB, fg=C_SUB,
                             bg=C_CARD)
    dia_now_label.pack(side="right")

    def on_dia_edit() -> None:
        """把初始晶钻数改成框里的值，写一笔晶钻日志并更新基线文件。

        按钮只有真的暂停（parked）时才可点，那时工作线程在空转，
        改计数器不会跟它打架。改完顺手把 stats 里的显示值也更新：
        工作线程还停着，不会马上盖掉，但界面不用等下一轮。
        """
        raw = dia_start_var.get().strip()
        try:
            n = int(raw)
            if n < 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("填得不对", "初始晶钻要填非负整数")
            return
        dia = stats.get("dia_obj")
        if dia is None:
            return
        dia.set_start(n)
        stats.update(dia_start=n,
                     dia_gain="" if dia.gained() is None else dia.gained())

    dia_edit_btn.config(command=on_dia_edit)

    btns = tk.Frame(inner3, bg=C_CARD)
    btns.pack(fill="x")

    pause_btn = tk.Button(btns, text="暂停", width=10, font=F_BODY,
                          relief="flat", bg=C_BLUE, fg="#ffffff",
                          activebackground=C_BLUE_DARK,
                          activeforeground="#ffffff", cursor="hand2",
                          state="disabled")
    pause_btn.pack(side="left")

    stop_btn = tk.Button(btns, text="收工", width=10, font=F_BODY,
                         relief="flat", bg=C_GOLD, fg=C_TEXT,
                         activebackground="#e9bd82", cursor="hand2",
                         state="disabled")
    stop_btn.pack(side="right")

    started = {"v": False}

    def on_start() -> None:
        """把配置存进 user.json，锁住配置卡，起工作线程。"""
        name = acc_var.get().strip() or "default"
        raw_target = target_var.get().strip()
        try:
            target = int(raw_target) if raw_target else None
            if target is not None and target <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("填得不对",
                                 "「最多尝试点击」要填正整数，留空表示不限")
            return

        data = user.load()
        data["current"] = name
        data.setdefault("accounts", {})[name] = {
            "fresh": fresh_var.get(),
            "purge": purge_var.get(),
            "manual": 0,          # 这个设置已经并进晶钻计数那颗数里
        }
        user.save(data)

        # 配置锁死：跑着的时候改这些没有意义，账号更是不能换。
        # 注意 dia_cb 不锁：晶钻计数是跑着的时候也能拨的开关
        for w in (acc_box, fresh_cb, purge_cb, limit_entry,
                  target_entry, list_cb, world_cb):
            w.config(state="disabled")
        start_btn.config(text="运行中", state="disabled")
        pause_btn.config(state="normal")
        stop_btn.config(state="normal")
        started["v"] = True

        threading.Thread(
            target=run_flow,
            args=(settings, stats, list_var.get(), world_var.get(), target),
            daemon=True).start()

    start_btn.config(command=on_start)

    def on_pause() -> None:
        # 只拨请求位，按钮文字和状态灯都交给 refresh 按真实现状画：
        # 工作线程没停稳（parked）之前，界面上任何地方都不许说「已暂停」
        settings.paused = not settings.paused

    def on_stop() -> None:
        # bot 在轮与轮（家与家）的边界上收尾，不会把半个拜访斩在中间
        settings.stop()
        stop_btn.config(text="收尾中…", state="disabled")

    pause_btn.config(command=on_pause)
    stop_btn.config(command=on_stop)
    select(settings.mode)
    on_dia_toggle()        # 初始联动：晶钻计数没勾时「新的一天」就是灰的
    on_flow_toggle()

    asking = {"full": False, "dia": False}     # 弹窗正在开着，别重复弹

    def ui_ask(prompt: str) -> int | None:
        """「问人」钩子的弹窗版。工作线程调它，主线程弹窗，
        靠 stats 里的 dia_ask / dia_answer 两个键一来一回传答案。
        prompt 是给人看的提示语，晶钻数和关注人数各用各的。"""
        stats["dia_ask"] = prompt
        while "dia_ask" in stats and not settings.stopped:
            time.sleep(0.2)
        return stats.pop("dia_answer", None)

    diamonds.ASK = ui_ask

    def refresh() -> None:
        """定时把 stats 里的状态搬到界面上，顺手看有没有事要问人。

        所有读都给默认值：第一轮还没跑完时这些键就不存在。
        界面不能因为缺个键就招 KeyError 把自己卡死。

        bot 要等人拍板的两件事都走 stats 放键、这里弹窗、删键放行的
        路子（弹窗必须开在主线程，tkinter 的硬规矩）：
            dia_full    晶钻到设定颗数，问继续刷还是收工
            dia_ask     开工时没识别到晶钻数，问人现在多少颗
        """
        if stats.get("dia_ask") and not asking["dia"]:
            asking["dia"] = True
            prompt = stats.get("dia_ask")
            ans = simpledialog.askinteger(
                "需要人工输入",
                str(prompt),
                minvalue=0, parent=root)
            stats.pop("dia_ask", None)
            stats["dia_answer"] = ans
            asking["dia"] = False

        full = stats.get("dia_full")
        if full and not asking["full"]:
            asking["full"] = True
            start, now = full
            go_on = messagebox.askyesno(
                "晶钻已到设定上限",
                f"今日开始时 {start} 钻，现在 {now} 钻，"
                f"已经满 {diamonds.DAILY_LIMIT} 上限。\n\n"
                f"要继续刷晶钻吗？\n"
                f"「是」继续刷（晶钻计数随之关闭，上限不再提醒）\n"
                f"「否」收工")
            if go_on:
                settings.count_diamonds = False
                dia_var.set(False)
                on_dia_toggle()
            else:
                settings.stop()
            stats.pop("dia_full", None)
            asking["full"] = False

        # 计数开关可能被工作线程改（满上限弹窗选了继续刷），
        # 发现两边不一致时以 Settings 为准，把勾拨过去
        if dia_var.get() != settings.count_diamonds:
            dia_var.set(settings.count_diamonds)

        # 晶钻栏。跑着的时候框跟着 stats 里的最新值走；真暂停时
        # 工作线程停住、数值不变，框留给用户改，这时千万不要再
        # 往框里写，不然用户打到一半的数会被刷掉
        parked_now = settings.paused and settings.parked
        dia = stats.get("dia_obj")
        can_edit = (started["v"] and parked_now and not settings.stopped
                    and dia is not None and dia.enabled)
        dia_start_entry.config(state="normal" if can_edit else "disabled")
        dia_edit_btn.config(state="normal" if can_edit else "disabled")
        if not can_edit:
            v = stats.get("dia_start", "")
            dia_start_var.set("" if v == "" else str(v))
        now_v = stats.get("dia_now", "")
        gain_v = stats.get("dia_gain", "")
        if now_v == "":
            dia_now_label.config(text="未计数" if started["v"] else "")
        elif gain_v == "":
            dia_now_label.config(text=f"现在 {now_v}")
        else:
            dia_now_label.config(text=f"现在 {now_v}（比开工 +{gain_v}）")

        # 状态指示：灰没开工，蓝在跑，金是暂停请求已收到但当前这家
        # 还没跑完（脚本还在点屏幕！），橙才是真的停稳了——和控制台
        # 那行「已暂停」是同一时刻，灰收尾。没停稳之前绝不说已暂停：
        # 用户看到「已暂停」就去动模拟器，脚本再点一下坐标就全乱了
        if settings.stopped:
            state_label.config(text="● 已收工", fg=C_DEAD)
        elif not started["v"]:
            state_label.config(text="● 待启动", fg=C_DEAD)
        elif settings.paused and settings.parked:
            state_label.config(text="● 已暂停", fg=C_WARN)
        elif settings.paused:
            state_label.config(text="● 暂停中…", fg=C_GOLD_DARK)
        else:
            state_label.config(text="● 运行中", fg=C_BLUE)

        # 按钮文字跟着真实现状走：没停稳时点是「取消这个暂停请求」，
        # 停稳了才是「继续」
        if settings.paused:
            pause_btn.config(
                text="继续" if settings.parked else "取消暂停")
        else:
            pause_btn.config(text="暂停")

        root.after(REFRESH_MS, refresh)

    refresh()


def main() -> None:
    """开面板。打开后什么都不跑，等用户在界面上选好配置点开始。"""
    # 高 DPI 屏（比如 125%、150% 缩放）上 Windows 默认把 tkinter 当成
    # 不感知 DPI 的老程序，整个窗口被位图拉伸，字就糊了。声明进程感知
    # DPI 之后按真实像素渲染，字就清楚了。必须抢在第一个窗口建立之前。
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        pass

    settings = modes.Settings()
    stats: dict = {}

    root = tk.Tk()

    # 量出当前屏幕的缩放倍数（96 DPI = 1.0，150% 缩放 = 1.5），
    # build 里所有按像素写死的尺寸都乘它
    global SCALE
    SCALE = root.winfo_fpixels("1i") / 96

    build(root, settings, stats)

    try:
        root.mainloop()
    finally:
        # 关窗口后让工作线程自己收尾。daemon 线程会跟着进程一起死，
        # 这里置一下 stopped 只是为了让它尽量在一轮的边界上退，
        # 而不是卡在半个拜访里被斩掉。
        settings.stop()


if __name__ == "__main__":
    main()

