"""多账号切换：现在刷的是哪个号，以及这个号的三项开工设置。

用法：
    python core/user.py                  看看当前是哪个号、三项设置是什么
    python core/user.py 小明             切到「小明」，三项重置为默认：是 是 0
    python core/user.py 小明 否 是 2     切号并显式指定三项
    python core/user.py . 否 是 2        不换号，只改当前号的设置

三项设置依次是：
    重新识别初始晶钻数    是：无视今天的基线和日志，现场读屏重设起点。
                        慎用：100 颗上限会从新起点重新算，已经吃过的
                        不再算数。默认是否，就是因为这条误触代价太大
                        否：今天这个号跑过就恢复基线和最近读数，
                        没跑过（新号/新的一天）就现场读屏
    未清理关注列表        是：这个号今天还没清过没饭的人，开跑时先清一遍
                        否：今天清过了，跳过每日清理
    已手工吃几口          开跑前这个号已经手动吃掉的口数。只服务命令行
                        直接跑的场景；面板已经没有这个输入，面板里
                        等价的事是改「晶钻计数满 __ 颗」那个数——
                        计划家数会按「100 减你填的数」自动减

设置存在 logs/user.json，按账号名各自存一份。晶钻日志和每日清理的
标记文件名里都带账号名，两个号同一天跑互不干扰。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

STATE = Path("logs") / "user.json"

# 切号时三项设置的默认值：不重新识别晶钻（安全路径：有记录就沿用
# 原起点，100 颗上限不会被重置；真想重设起点得显式改成是）、
# 未清理列表、手工吃 0 口
DEFAULTS = {"fresh": False, "purge": True, "manual": 0}

YES = {"是", "y", "yes", "1", "true"}
NO = {"否", "n", "no", "0", "false"}


def load() -> dict:
    """读整个状态文件。读不了就当作还一个号都没有。"""
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"current": "", "accounts": {}}


def save(data: dict) -> None:
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                     encoding="utf-8")


def current() -> dict:
    """当前账号的名字和三项设置，形如 {"name": "小明", "fresh": True, ...}。

    还一个号都没切过时返回名字叫 default 的隐形账号，设置全默认，
    行为跟引入多账号之前一致。
    """
    data = load()
    name = data.get("current") or "default"
    st = dict(DEFAULTS)
    st.update(data.get("accounts", {}).get(name, {}))
    st["name"] = name
    return st


def parse_bool(raw: str) -> bool | None:
    """把 是/否/y/n/1/0 之类的输入翻成布尔。看不懂返回 None。"""
    raw = raw.strip().lower()
    if raw in YES:
        return True
    if raw in NO:
        return False
    return None


def show(st: dict) -> None:
    print(f"当前账号：{st['name']}")
    print(f"  重新识别初始晶钻数：{'是' if st['fresh'] else '否'}")
    print(f"  未清理关注列表：{'是' if st['purge'] else '否'}")
    print(f"  已手工吃：{st['manual']} 口")


def main() -> None:
    args = sys.argv[1:]
    data = load()

    if not args:
        show(current())
        return

    # 第一个参数：账号名，点号表示不换号只改设置
    if args[0] != ".":
        name = args[0]
        data["current"] = name
        if name not in data.setdefault("accounts", {}):
            data["accounts"][name] = dict(DEFAULTS)
        elif len(args) == 1:
            # 切回一个老账号：三项重置为默认，跟切新号一致
            data["accounts"][name] = dict(DEFAULTS)
    elif not data.get("current"):
        sys.exit("还没有当前账号，先 python core/user.py <名字> 切一个")

    name = data["current"]
    st = data["accounts"].setdefault(name, dict(DEFAULTS))

    # 后三个参数：给了就改，没给不动
    if len(args) >= 2:
        v = parse_bool(args[1])
        if v is None:
            sys.exit(f"第二项要填 是 或 否，看不懂 {args[1]!r}")
        st["fresh"] = v
    if len(args) >= 3:
        v = parse_bool(args[2])
        if v is None:
            sys.exit(f"第三项要填 是 或 否，看不懂 {args[2]!r}")
        st["purge"] = v
    if len(args) >= 4:
        if not args[3].isdigit():
            sys.exit(f"第四项要填非负整数，看不懂 {args[3]!r}")
        st["manual"] = int(args[3])

    save(data)
    out = dict(DEFAULTS)
    out.update(st)
    out["name"] = name
    show(out)


if __name__ == "__main__":
    main()
