#!/usr/bin/env python3
"""记忆P4 · 睡前巩固——抄 Letta sleep-time agent 的 memory_rethink：
不在人干活的当口整理记忆，而是每天第一场对话收尾后，后台把每个人隔夜攒下的东西重新想一遍：

- 原则区 ← 从船主教导+流水里**蒸馏**做事原则（合并同类、去一次性细节）。教导原文不动——那是船主的话。
- 近况区 ← 压缩成当前状态一小段。
- 流水永远不动：原始层只增不删（后台橡皮擦在船主手里，P3）。

节流：每人每天最多巩固一次（.巩固状态.json 记上次日期）；没攒下东西的人跳过，不空烧。
模型：同抽取，走文案岗最便宜的一台。失败只落日志。
"""
from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path

from 根 import 数据根

COMPANY = 数据根
状态文件 = COMPANY / "记忆库" / ".巩固状态.json"

_巩固规矩 = """你在帮「{人名}」（岗位：{岗位}）整理记忆。

# 铁律（抄 Zep / 生成式agent 验证过的做法，别再整段重写——那会像复印件再复印，越理越歪）
老原则**默认原样保留**，绝不因为"最近没提到"就删；也**不要把旧原则重新措辞**。你只做下面两件事，其余系统自动原样留。

# 你的两件事
1. **新增原则**：从【船主教导】和【最近流水】里，挑出**真发生过、值得长期守**的新做事原则。每条必须在流水/教导里有实据；一次性的、随口的、拿不准的、已有原则里已覆盖的，**都不要**。也不许否定做事根本底线（认真测试/如实验收/安全/诚实/守船主边界）。没有就空列表。
2. **应删原则**：只挑【已有原则】里**被最近流水明确反驳/推翻**的那几条，把要删的**整行原样抄回来**。**只有显式矛盾才删；"最近没提到"绝不是删的理由。** 没有就空列表。

# 近况
另写一句 ≤120 字的当前状态（他最近在干什么、什么状态），从流水看，别编。

# 已有原则（只读；用来判重和挑"应删"，别重写它们）
{旧原则}

# 船主教导（新增原则的可信原料）
{教导}

# 最近流水（新增/应删的判据）
{流水}

# 输出（只输出 JSON，不要解释、不要代码围栏）
{{"新增原则": ["- ...", ...], "应删原则": ["- 要删的旧原则整行", ...], "近况": "..."}}"""


def _读状态() -> dict:
    try:
        return json.loads(状态文件.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _写状态(状态: dict) -> None:
    状态文件.parent.mkdir(parents=True, exist_ok=True)
    状态文件.write_text(json.dumps(状态, ensure_ascii=False, indent=1), encoding="utf-8")


_原则压缩规矩 = """下面是「{人名}」攒下的做事原则，条数多了、有近似重复。合并去重、收敛成更干净的一组**当前**原则：
- 语义重复/近似的合并成一条，**保留信息量、别丢细节、别改成空话**。
- 明显自相矛盾的，留最贴近近期的那条。
- **不新增输入里没有的原则**；拿不准的保留，宁多勿误删。
- 每条一行"- "开头。

# 原原则
{原则}

# 输出（只输出 JSON，不要解释、不要围栏）
{{"原则": ["- ...", ...]}}"""


async def _压缩原则(行们: list, 建Agent, 抽取模型岗: str, 人名: str) -> list:
    """增长封顶(抄 SSGM 巩固压缩)：只在原则真攒多了才跑一次去重合并；护栏触发原样返回。
    平时纯增量(不重写→不漂)，只有胀了才收敛一次，把"漂移机会"降到最低。"""
    from agentscope.message import Msg, TextBlock
    from 记忆抽取 import _剥JSON
    a = 建Agent(抽取模型岗, _原则压缩规矩.format(人名=人名, 原则="\n".join(行们)),
                名字="原则压缩器", 最大tokens=2048, 思考=False)
    r = await a.reply(Msg(name="办公室", content=[TextBlock(type="text", text="压缩原则。")], role="user"))
    c = getattr(r, "content", "")
    文 = c if isinstance(c, str) else "".join(
        (b.get("text") if isinstance(b, dict) else getattr(b, "text", "")) or "" for b in (c or []))
    候 = _剥JSON(文).get("原则")
    if not isinstance(候, list):
        return 行们
    新 = [f"- {x.lstrip('- ').strip()}" for x in 候 if isinstance(x, str) and x.strip()]
    if len(新) < max(8, len(行们) // 4):  # 压缩后太少(疑似截断/误删)→原样保留
        return 行们
    return 新


async def 巩固一个人(人名: str, 岗位: str) -> bool:
    from agentscope.message import Msg, TextBlock
    from 记忆抽取 import _剥JSON, 抽取模型岗
    from 活_本人 import _读记忆区, 重写某人区
    from 升级_模型层 import 建Agent

    区 = _读记忆区(人名)
    if not (区["流水"].strip() or 区["船主教导"].strip()):
        return False  # 没攒下东西，不空烧
    旧原则行 = [l for l in 区["原则"].splitlines() if l.strip()]
    系统 = _巩固规矩.format(
        人名=人名, 岗位=岗位,
        旧原则="\n".join(旧原则行) or "（空）",
        教导=区["船主教导"].strip() or "（空）",
        流水="\n".join([l for l in 区["流水"].splitlines() if l.strip()][-40:]) or "（空）",
    )
    a = 建Agent(抽取模型岗, 系统, 名字="记忆巩固器", 最大tokens=2048, 思考=False)
    r = await a.reply(Msg(name="办公室", content=[TextBlock(type="text", text="开始整理。")], role="user"))
    c = getattr(r, "content", "")
    文 = c if isinstance(c, str) else "".join(
        (b.get("text") if isinstance(b, dict) else getattr(b, "text", "")) or "" for b in (c or [])
    )
    结果 = _剥JSON(文)
    新增 = [f"- {str(x).lstrip('- ').strip()}" for x in (结果.get("新增原则") or [])
            if isinstance(x, str) and x.strip()]
    应删 = {str(x).strip() for x in (结果.get("应删原则") or []) if isinstance(x, str) and x.strip()}
    新近况 = (结果.get("近况") or "").strip()

    # 增量合并(抄 Zep 矛盾才失效 + 生成式agent 只加不重写)：老原则原样留，
    # 只删被"应删"精确点名的(一晚最多删2条，防误删)；再加去重后的新增。绝不整段重写、绝不因"最近没提到"就删。
    保留 = [l for l in 旧原则行 if l.strip() not in 应删]
    if len(旧原则行) - len(保留) > 2:  # 删太多→疑似误判，本晚一条不删
        保留 = list(旧原则行)
    合并 = list(保留)
    保留文 = "\n".join(合并)
    for n in 新增:
        核 = n.lstrip("- ").strip()
        if 核 and n not in 合并 and 核 not in 保留文:  # 粗去重，别重复已有
            合并.append(n)
    # 增长封顶：原则攒过 40 条才压缩一次(平时纯增量不重写→不漂；胀了才收敛一次)
    if len(合并) > 40:
        try:
            合并 = await _压缩原则(合并, 建Agent, 抽取模型岗, 人名)
        except Exception:  # noqa: BLE001
            pass
    新原则文 = "\n".join(合并).strip()

    动 = False
    if 新原则文 and 新原则文 != "\n".join(旧原则行):
        重写某人区(人名, "原则", 新原则文)
        动 = True
    if 新近况:
        重写某人区(人名, "近况", f"（{_dt.date.today().isoformat()} 巩固）{新近况}")
        动 = True
    return 动


def 巩固到期的人() -> None:
    """同步入口（后台线程用）：每人每天最多一次。错误只落日志，不外抛。"""
    import asyncio

    from 活_本人 import _岗位的人
    from 升级_模型层 import 全部岗位

    日志 = COMPANY / "记忆库" / "抽取日志.md"
    日志.parent.mkdir(parents=True, exist_ok=True)
    try:
        import 会话摘要
        会话摘要.补齐摘要()  # 补齐所有落下的天(治船主间歇使用:忙十天半个月才回来,别丢中间活跃日的摘要)
    except Exception as e:  # noqa: BLE001
        with 日志.open("a", encoding="utf-8") as f:
            f.write(f"- [{_dt.datetime.now().strftime('%Y-%m-%d %H:%M')}] 会话摘要：挂钩失败 {type(e).__name__}: {e}\n")

    # 图谱遗忘（艾宾浩斯衰减，睡前顺手跑一次）：久未命中的边淡出活跃层，压住无界增长；pinned/亲口的豁免不淡。
    try:
        import 图记忆
        淡 = 图记忆.衰减清理()
        if 淡:
            with 日志.open("a", encoding="utf-8") as f:
                f.write(f"- [{_dt.datetime.now().strftime('%Y-%m-%d %H:%M')}] 图谱遗忘：淡出 {淡} 条久未命中的边\n")
    except Exception as e:  # noqa: BLE001
        with 日志.open("a", encoding="utf-8") as f:
            f.write(f"- [{_dt.datetime.now().strftime('%Y-%m-%d %H:%M')}] 图谱遗忘：跑失败 {type(e).__name__}: {e}\n")

    今天 = _dt.date.today().isoformat()
    状态 = _读状态()
    # 船主走势：每天蒸一次「当前状态基调」（往上/往下/反复的压力源），独立于有没有员工到期。复用同一台睡前引擎。
    if 状态.get("__船主__") != 今天:
        try:
            import 船主记忆
            _l = asyncio.new_event_loop()
            asyncio.set_event_loop(_l)
            动 = _l.run_until_complete(船主记忆.巩固船主())
            _l.close()
            if 动:
                状态["__船主__"] = 今天
                _写状态(状态)
                with 日志.open("a", encoding="utf-8") as f:
                    f.write(f"- [{_dt.datetime.now().strftime('%Y-%m-%d %H:%M')}] 船主走势：状态基调已重蒸\n")
        except Exception as e:  # noqa: BLE001
            with 日志.open("a", encoding="utf-8") as f:
                f.write(f"- [{_dt.datetime.now().strftime('%Y-%m-%d %H:%M')}] 船主走势：巩固失败 {type(e).__name__}: {e}\n")
    到期 = [(人, 岗) for 岗 in 全部岗位() if (人 := _岗位的人(岗)) and 状态.get(人) != 今天]
    if not 到期:
        return
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    for 人, 岗 in 到期:
        try:
            动了 = loop.run_until_complete(巩固一个人(人, 岗))
            if 动了:
                状态[人] = 今天
                with 日志.open("a", encoding="utf-8") as f:
                    f.write(f"- [{_dt.datetime.now().strftime('%Y-%m-%d %H:%M')}] {人}：睡前巩固完成（原则/近况已重整）\n")
        except Exception as e:  # noqa: BLE001
            with 日志.open("a", encoding="utf-8") as f:
                f.write(f"- [{_dt.datetime.now().strftime('%Y-%m-%d %H:%M')}] {人}：巩固失败 {type(e).__name__}: {e}\n")
    _写状态(状态)
    try:
        loop.run_until_complete(loop.shutdown_asyncgens())
    except Exception:  # noqa: BLE001
        pass
    loop.close()
