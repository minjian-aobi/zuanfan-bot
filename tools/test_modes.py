"""modes.py 的纯逻辑测试。不连模拟器，不需要 opencv，直接：

    python tools/test_modes.py

全部通过时只打一行 OK。改过档位逻辑后先跑这个，再连模拟器跑真流程。
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

import modes as m


def eq(got, want, what):
    assert got == want, f"{what}：得到 {got!r}，应为 {want!r}"


# normalize：越界和非整数都退到 MODE_START
for bad in (-1, 2, 3, 7, None, "x"):
    eq(m.normalize(bad), m.MODE_START, f"normalize({bad!r})")
eq(m.normalize(1.5), m.MODE_FOLLOW_MEMORY, "1.5 取整后是 1，不走退回分支")
for ok in (0, 1):
    eq(m.normalize(ok), ok, f"normalize({ok})")
eq(m.MODE_COUNT, 2, "只有两档")
eq(len(m.MODE_NAMES), m.MODE_COUNT, "MODE_NAMES 条数")
eq(len(m.MODE_DESCS), m.MODE_COUNT, "MODE_DESCS 条数")

# 默认档是第二档
eq(m.MODE_START, m.MODE_FOLLOW_MEMORY, "默认档")

# skip_before_enter：只有第一档碰到粉丝行才提前跳过
eq(m.skip_before_enter(m.MODE_SKIP_FAN, True), True, "第一档粉丝行")
eq(m.skip_before_enter(m.MODE_SKIP_FAN, False), False, "第一档非粉丝行")
eq(m.skip_before_enter(m.MODE_FOLLOW_MEMORY, True), False, "第二档粉丝行")

# card_action：第一档走到资料卡时已确认不是粉丝，关注不参与判断
eq(m.card_action(m.MODE_SKIP_FAN, False, True, False), m.ACT_ENTER,
   "第一档直接进门")

# 第二档：取关按钮在就跳，关注按钮在就先关注，两个都不在就直接进门
eq(m.card_action(m.MODE_FOLLOW_MEMORY, False, False, True), m.ACT_SKIP,
   "第二档见取关就跳")
eq(m.card_action(m.MODE_FOLLOW_MEMORY, False, True, False),
   m.ACT_FOLLOW_THEN_ENTER, "第二档见关注就先关注")
eq(m.card_action(m.MODE_FOLLOW_MEMORY, False, False, False), m.ACT_ENTER,
   "第二档两个按钮都没命中时宁可多跑")
# 模板失配导致两个按钮同时命中时，取关优先，宁可漏一家也不反复进同一家
eq(m.card_action(m.MODE_FOLLOW_MEMORY, False, True, True), m.ACT_SKIP,
   "第二档两个按钮都命中")

# 需不需要多跑一次模板匹配
eq([m.needs_unfollow_check(i) for i in (0, 1)], [False, True],
   "needs_unfollow_check")
eq([m.needs_fan_check(i) for i in (0, 1)], [True, False],
   "needs_fan_check")

# Settings：越界值进去也会被收到合法档位
s = m.Settings()
eq(s.mode, m.MODE_START, "Settings 默认档")
s.mode = 9
eq(s.mode, m.MODE_START, "Settings 越界档退默认")
s.mode = 0
eq(s.mode, 0, "Settings 改档")
eq(s.paused, False, "Settings 初始未暂停")
s.paused = True
eq(s.paused, True, "Settings 暂停")
eq(s.stopped, False, "Settings 初始未停止")
s.stop()
eq(s.stopped, True, "Settings 停止")

# 终端打印用的档位描述从 1 数
eq(m.mode_label(0).startswith("1 "), True, "mode_label 从 1 数")
eq(m.mode_label(1).startswith("2 "), True, "mode_label 第二档")

print("OK modes")

