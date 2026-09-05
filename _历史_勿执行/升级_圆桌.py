#!/usr/bin/env python3
"""开发公司 · 整合升级层 — 多模型圆桌（项目会）。【第3步:接各厂官方 ChatModel 底子】

形态不变（验证过的圆桌）：会前议题 → 各自发言 → 项目经理判够不够/缺不缺证据（质量判据动态收敛，
轮数判出来不写死）→ 缺证据查证不编造（查不到→请示船主、不硬出工单）→ 汇总决议+工单。

2.0.2 适配：v1.0 的 MsgHub/fanout_pipeline 已不在原位。改用**手动并发**（asyncio.gather 跑各 Agent）
+ **显式传桌面**（把别人的发言拼进 prompt）——这正是原 会议.py 线程版的做法，更稳、不依赖被重构的模块。
岗位发言体 = 迁移_模型层.建Agent(口语 system_prompt)。项目经理判断 = 一个临时 PM Agent。

落盘/校验复用 会议.py 纯函数（框架无关，不重写、不碰旧同步引擎）。
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
from typing import Any

import 引擎工具 as 巧  # noqa: E402  瞬时停止键:开会中也要能当场停(看急停旗)
from 会议 import (  # 复用纯函数与路径  # noqa: E402
    待确认, 会议目录, 初始化, _安全名, _编号, _提取_json, _校验, _去重, _紧凑, _尝试查阅, _写子工单, _渲染项目会,
)
from 升级_模型层 import 建Agent, 可参会岗位  # noqa: E402（第3步:各厂官方 ChatModel）
from agentscope.state import AgentState  # noqa: E402
from agentscope.permission import PermissionContext, PermissionMode  # noqa: E402

try:
    from agentscope.message import Msg, TextBlock
except ImportError as e:  # noqa: BLE001
    raise ImportError(
        "未安装 AgentScope（2.0.x）。请先按『部署_AgentScope.md』执行 `pip install agentscope`。"
        f" 原始错误：{e}"
    ) from e


发言人格: dict[str, tuple[str, str]] = {
    "项目经理": ("老钟", "专业上你最看重事情能不能定下来、推进下去,该拍板时不让它悬着"),
    "首席工程师": ("老梁", "专业上你最看重方案是不是最简、有没有过度设计和不必要的开销"),
    "工程师": ("阿强", "专业上你最看重能不能真跑起来、怎么实现最实在、性能扛不扛得住"),
    "测试工程师": ("老纪", "专业上你最先想的是这东西会怎么坏——出错、丢数据、被误用,底线是出事时证据还在"),
    "文案工程师（兼职）": ("阿言", "专业上你最看重别人看不看得懂、清不清楚、跟事实对不对得上"),
}


def 发言名(岗位: str) -> str:
    return 发言人格.get(岗位, (岗位, ""))[0]


def 发言上下文(岗位: str) -> str:
    """开会发言:给只读查询能力 + 指路公司现成资料 + 引导敢拍/不懂就问。不灌培训材料、不立格式禁令。"""
    名, 视角 = 发言人格.get(岗位, (岗位, ""))
    return (
        f"你是{名}，开发公司的{岗位}，{视角}。\n"
        "开会讨论船主交代的事。发言前，先用工具翻公司里现成的资料，把背景和事实弄清楚：\n"
        "  · 公司是谁、在为什么干活 → 读 `公司是谁_为什么干活.md`\n"
        "  · 各岗位的职责/技能设定 → 读 `岗位/<岗位名>/说明书.md`、`技能/`\n"
        f"  · 【你的经验 + 船主以前教过你的偏好(比如要说人话、别太长)】→ 读 `岗位/{岗位}/记忆.md`，照船主教过的来\n"
        "查得到就自己查；查不到、拿不准船主到底要什么、碰到没听过的新东西，"
        "就老实说『要问船主：X』——不懂就问，这没什么不好，别编、别绕、别装懂。\n"
        "基于你查到的和你的专业，把你的分析讲清楚（理性分析，也理解这事对她的分量）。\n"
        "【怎么说】像跟同事当面聊那样说人话——别堆报告、别长篇大表格、别一二三四罗列；"
        "几句话把要点讲透即可。遇到关键名词或不好懂的地方，顺手一句解释或举个例子，"
        "确保船主(不是工程师)一眼能看懂。"
    )


def _发言查询工具集():
    """开会发言时给岗位只读查询能力:翻公司现成资料(EXPLORE 只读模式,改不了任何东西)。

    用净Glob(搜索工具.py):像 ripgrep/git 那样遍历时剪掉 .venv/node_modules/官方参考 等依赖垃圾,
    岗位能搜全公司、应付任何任务,又快又不趟 7 万依赖文件。Grep 走 ripgrep 本就认 gitignore。"""
    from 搜索工具 import 资料工具集
    return 资料工具集()


def _块文本(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    out: list[str] = []
    for b in content or []:
        t = b.get("text") if isinstance(b, dict) else getattr(b, "text", None)
        typ = b.get("type") if isinstance(b, dict) else getattr(b, "type", None)
        if t and (typ in (None, "text")):
            out.append(str(t))
    return "".join(out).strip()


def _进度(progress, text: str) -> None:
    if not progress:
        return
    try:
        progress(text)
    except Exception:  # noqa: BLE001
        pass


def _发言事件(r: str, 种类: str, 值: str) -> dict:
    """把 _问agent流式 的过程事件(种类:正文/思考/工具)映射成前端事件,带上发言人。"""
    if 种类 == "思考":
        return {"类型": "思考增量", "发言人": r, "delta": 值}
    if 种类 == "工具":
        return {"类型": "工具动态", "发言人": r, "工具": 值}
    return {"类型": "发言增量", "发言人": r, "delta": 值}


async def _问agent(agent, prompt: str, on_event=None) -> str:
    """给一个 Agent 发一句话，取回纯文本。复用 _问agent流式 → 自动获得思考/工具过程 + 瞬时停止监视。

    项目经理的议题准备/评估/汇总都走这条:①能瞬时停(监视急停旗→interrupt,补"准备议题"阶段的停止盲区)
    ②给 on_event 时把'思考'/'工具'过程亮给前端(议题准备不再 58 秒黑箱);正文(可能是 JSON)不亮、只累积返回。"""
    def _仅过程(种类: str, 值: str) -> None:
        if on_event and 种类 in ("思考", "工具"):
            on_event(种类, 值)
    return await _问agent流式(agent, prompt, _仅过程)


async def _问agent流式(agent, prompt: str, on_event) -> str:
    """流式问 Agent，把过程亮给前端（不再黑箱）。on_event(种类, 值)，种类：
       '正文'=发言 delta(打字机) / '思考'=推理流(ThinkingBlockDelta) / '工具'=正在调的工具名(ToolCallStart)。
    依据(查证有据)：官方 web_ui 的 MessageBubble 就是把 agent 拆成 thinking/tool_call/text 块分层流式；
    业界共识：等待期亮出中间步骤(在想什么、查什么)可把感知延迟降 60-90%。思考期用户看见它在想/在查，不空等。
    返回完整正文发言。瞬时停止键监视不变(后台看急停旗→interrupt 当场掐断含思考中)。"""
    from agentscope.event import TextBlockDeltaEvent, ThinkingBlockDeltaEvent, ToolCallStartEvent

    完整: list[str] = []
    停 = asyncio.Event()

    async def 监视停止() -> None:
        while not 停.is_set():
            if 巧.急停文件.exists():
                try:
                    agent.interrupt()
                except Exception:  # noqa: BLE001  2.0.2 若无 interrupt,退回发言间的旗检查兜底
                    pass
                return
            await asyncio.sleep(0.4)

    def _亮(种类: str, 值: str) -> None:
        try:
            on_event(种类, 值)
        except Exception:  # noqa: BLE001
            pass

    watcher = asyncio.create_task(监视停止())
    try:
        async for ev in agent.reply_stream(Msg(name="主持人", content=[TextBlock(type="text", text=prompt)], role="user")):
            if 巧.急停文件.exists():  # 流式中随时断(interrupt 兜底:有新块到就停)
                break
            if isinstance(ev, TextBlockDeltaEvent):
                d = getattr(ev, "delta", "") or ""
                if d:
                    完整.append(d)
                    _亮("正文", d)
            elif isinstance(ev, ThinkingBlockDeltaEvent):  # 思考流:推理过程,前端浅灰显示"它在想什么"
                d = getattr(ev, "delta", "") or ""
                if d:
                    _亮("思考", d)
            elif isinstance(ev, ToolCallStartEvent):  # 工具调用:正在查哪个文件/搜什么,前端显示"正在查 xx"
                nm = getattr(ev, "tool_call_name", "") or ""
                if nm:
                    _亮("工具", nm)
    except asyncio.CancelledError:
        pass  # 被停止键 interrupt——返回已说出的部分,不当成发言失败
    finally:
        停.set()
        watcher.cancel()
    return "".join(完整) or "（没说话）"


async def _问项目经理(prompt: str, 最大tokens: int = 2048, on_event=None) -> str:
    """项目经理的结构化判断：用一个临时 PM Agent（system_prompt 极简，只按要求输出）。
    on_event 给时把思考/工具过程亮给前端（议题准备不再黑箱）。"""
    pm = 建Agent("项目经理", "你是开发公司的项目经理。严格按用户要求输出，需要 JSON 时只输出 JSON。",
                 最大tokens=最大tokens, 名字="项目经理")
    return await _问agent(pm, prompt, on_event=on_event)


async def _会前议题(需求: str, progress=None) -> dict[str, Any]:
    # 议题准备过程亮给前端(治"准备议题 58 秒黑箱"):思考/工具以"项目经理"为发言人推出
    on_event = (lambda 种, 值: _进度(progress, _发言事件("项目经理", 种, 值))) if progress else None
    prompt = (
        "你是开发公司的项目经理。现在只做项目会会前准备，不生成工单。决定哪些岗位参加并给讨论议题。只输出JSON对象。\n"
        "JSON字段: 标题, 议题, 覆盖说明, 主持说明, 参会, 讨论问题, 资料需求, 验收关注, 风险关注。\n"
        "参会只能从 首席工程师/工程师/文案工程师（兼职）/测试工程师 中选真正相关的；一般至少要有执行岗与验收/反证岗。"
        "不要把船主或项目经理放进参会。\n\n"
        f"船主需求:\n{需求}"
    )
    try:
        data = _提取_json(await _问项目经理(prompt, 最大tokens=2048, on_event=on_event))
    except Exception:  # noqa: BLE001
        data = {"标题": "办公室任务", "议题": "项目会", "覆盖说明": "会前准备输出不合格，改保守配置。",
                "主持说明": "先由执行与验收岗位分别说明理解、资料需求、风险和验收标准。",
                "参会": ["文案工程师（兼职）", "测试工程师"],
                "讨论问题": ["需求怎么理解", "需要哪些资料", "验收如何证明", "是否有权限或预算风险"],
                "资料需求": [], "验收关注": [], "风险关注": ["会前议题JSON解析失败"]}
    for k, v in {"标题": "办公室任务", "议题": data.get("标题", "项目会"), "覆盖说明": "", "主持说明": "",
                 "讨论问题": [], "资料需求": [], "验收关注": [], "风险关注": []}.items():
        data.setdefault(k, v)
    data["参会"] = [x for x in _去重([str(x) for x in data.get("参会", [])]) if x in 可参会岗位] \
        or ["文案工程师（兼职）", "测试工程师"]
    return data


async def _评估收敛(发言流: list[dict[str, str]]) -> dict[str, Any]:
    桌面 = "\n\n".join(f"{s.get('发言人')}（{s.get('类型', '')}）：{s.get('内容')}" for s in 发言流)
    prompt = (
        "你是项目经理，在主持这场会。下面是目前为止的全部发言。\n\n" + 桌面
        + "\n\n判断并只输出JSON。先守两条原则：\n"
        + "① 公司可能刚起步——没有历史记录、复盘、审批、过往项目这类『积累型/历史型』资料是正常的。"
        + "**绝不能因为『没有历史绩效、没有历史记录、记忆为空』就停下问船主**；这种情况要基于现成的岗位职责、技能设定和常识【直接判断、敢拍】。\n"
        + "② 只有这种才算真要问船主：船主的需求本身没说清，或出现了谁都不知道、公司里也查不到、且只有船主能解释的关键信息，不补就完全没法往下。\n\n"
        + "判：\n"
        + "1) 有没有人卡在【真要问船主】(按原则②,不是缺历史积累)？有就把要问的列进 缺证据。\n"
        + "2) 否则,讨论够不够支撑你拍板出决议和工单？能基于现成资料和专业判断的,就填 够了=true,别拖着不拍。\n"
        + "JSON：{\"缺证据\": [\"真要问船主的\"], \"够了\": true, \"理由\": \"一句话\"}。"
    )
    try:
        d = _提取_json(await _问项目经理(prompt, 最大tokens=2048))
        缺 = d.get("缺证据") or []
        if not isinstance(缺, list):
            缺 = [str(缺)]
        return {"缺证据": [str(x) for x in 缺 if str(x).strip()], "够了": bool(d.get("够了")), "理由": str(d.get("理由", ""))}
    except Exception:  # noqa: BLE001
        return {"缺证据": [], "够了": True, "理由": "评估输出无法解析，默认收敛、不空转"}


def _默认类型(岗位: str) -> str:
    return {"测试工程师": "验收", "文案工程师（兼职）": "方案", "工程师": "风险", "首席工程师": "方案"}.get(岗位, "发言")


def _是叫停(s: str) -> bool:
    return any(w in s for w in ("停下", "叫停", "暂停", "打住", "别说了", "停！", "停。"))


def _取新插话(插话源) -> list[str]:
    """债②:优先从实时插话源(事件总线队列回调)取本步新插话(立刻生效);无源则回退文件轮询(旧 /kickoff_stream 用)。"""
    if 插话源 is not None:
        try:
            return [s for s in 插话源() if s]
        except Exception:  # noqa: BLE001
            return []
    try:
        p = 会议目录.parent / "运行状态" / "船主插话.txt"
        if p.exists():
            t = p.read_text(encoding="utf-8").strip()
            p.unlink()
            return [t] if t else []
    except Exception:  # noqa: BLE001
        pass
    return []


async def _圆桌讨论(需求: str, plan: dict[str, Any], roles: list[str], progress=None, 上限: int = 4, 插话源=None, 已有发言: list[dict[str, str]] | None = None) -> list[dict[str, str]]:
    """圆桌：会议真谛要求【顺序发言+互相听+观点动态变】——故 for r in roles 顺序 await(绝不并行gather,
    并行=各说各的=杀死互驳补盲)。各岗持久 Agent(连续团队成员),每轮只喂"它上次发言后的新增"(增量喂法),
    它记忆里已有更早的→真听全程不漏不重。质量判据动态收敛(PM判够不数轮),硬上限兜底。

    债⑥:已有发言非空=续会从断点续——用它初始化 全部,岗位接着已有发言往下说(不重跑),船主回话已注入其中。"""
    议题 = str(plan.get("议题") or "").strip()
    问题 = "、".join(str(x) for x in (plan.get("讨论问题") or [])[:5])
    # 岗位发言体：乙方案——每岗一个【持久】Agent(连续的团队成员,记得自己全部推理与所学),
    # 各自独立 AgentState(独立记忆,互不串)。真听=每轮只把"它上次发言后新增的发言(增量)"喂它,
    # 它记忆里已含此前一切→ memory + 增量 = 全程完整:不漏听、不"两份重复记忆"、不白读文件白烧token。
    # crash 已由模型层 context_size=1M 根治,持久累积在 80万触发线下远够不着。(会议真谛:团队非工具)
    _查工具 = _发言查询工具集()  # 工具无状态,可共享
    agents = {r: 建Agent(r, 发言上下文(r), 名字=发言名(r), toolkit=_查工具, 预算步数=16,
                         state=AgentState(permission_context=PermissionContext(mode=PermissionMode.EXPLORE)),
                         stream=True) for r in roles}
    已喂 = {r: 0 for r in roles}  # 每个 agent 已消费到 全部 的第几条(增量喂法,不重复喂)

    全部: list[dict[str, str]] = list(已有发言) if 已有发言 else []  # 债⑥:续会时从断点(已有发言)接着,不从头
    待船主问题: list[str] = []
    收敛 = {"够了": False, "理由": ""}
    轮 = 0
    while 轮 < max(1, 上限):
        轮 += 1
        本轮有新插话 = False  # 船主插话=掌舵:本轮一旦有插话,强制再走一轮让全员都回应、调整,不许直接收敛(轮内无新插话即自然终止)
        for r in roles:  # ②顺序发言:每人带本场至今的发言(取要点控token),点名回应、不重复
            if 巧.急停文件.exists():  # 瞬时停止键:开会中也当场停(债A·停止管"开会"这条线),不留待续残记录
                _进度(progress, "⏸ 已停止本次会议。")
                return 全部, ["__停止__"]
            # 债②持续会话:每个发言前取船主实时插话/叫停(事件总线队列→立刻生效,不等下一轮、不靠文件轮询)
            for _插 in _取新插话(插话源):
                if _是叫停(_插):
                    全部.append({"发言人": "船主", "类型": "叫停", "内容": _插})
                    _进度(progress, f"⏸ 船主叫停，会议中止：{_插}")
                    return 全部, [f"船主已叫停本次会议：{_插}"]  # 非空→走暂停态,不汇总出结论/工单
                全部.append({"发言人": "船主", "类型": "插话", "内容": _插})
                本轮有新插话 = True
                _进度(progress, f"🗣 船主插话：{_插}（全员都要回应、调整）")
            # 乙·增量喂法:只喂它上次发言后新增的发言(更早的它记忆里已有)→真听全程+不重复
            增量 = 全部[已喂[r]:]
            新发言 = "\n\n".join(f"{发言名(s['发言人'])}（{s['发言人']}）：{s['内容']}" for s in 增量)
            if not 全部:  # 全场第一个开口
                提示 = (f"船主交代的事：{需求}\n"
                        + (f"这次会上讨论：{议题}\n" if 议题 and 议题 != 需求 else "")
                        + (f"几个要点：{问题}\n" if 问题 else "")
                        + "\n你第一个说，讲讲你的判断。")
            elif not 新发言:  # 轮到你但你上次发言后没人说新东西(罕见):请你接着推进
                提示 = "还是这件事，轮到你。基于目前讨论，把你的判断往前推进一步。"
            else:  # bug6 砍车轱辘:认同一句带过、只说增量、不强制反对
                提示 = (f"船主交代的事：{需求}\n\n你上次发言之后，大家新说的——\n\n{新发言}\n\n"
                        + "轮到你。**真听了再说**：认同就一句『前面的我都认同』带过，"
                        + "别逐条复述谁说过什么——那是车轱辘话、浪费时间。"
                        + "**只说你的增量**：你要补充/补全什么、你的看法因为听了大家而变成了什么、"
                        + "或你要反对谁的哪点并给出理由。没有要补的就直说『我没有要补的』，不必为发言而硬找不同。")
            _进度(progress, {"类型": "发言开始", "发言人": r, "轮": 轮})  # 前端新建空气泡+进度卡显示第几轮/谁在说
            try:
                out = str(await _问agent流式(
                    agents[r], 提示,
                    lambda 种, 值, _r=r: _进度(progress, _发言事件(_r, 种, 值))))
            except Exception as e:  # noqa: BLE001
                out = f"（{发言名(r)}发言失败：{e}）"
            全部.append({"发言人": r, "类型": _默认类型(r) if 轮 == 1 else "回应", "内容": out[:2000]})
            已喂[r] = len(全部)  # 它已消费到此(含自己刚说的,在自己记忆里),下轮只喂之后的新增
            _进度(progress, {"类型": "发言结束", "发言人": r, "内容": out})  # 气泡定稿(完整文本)

        # 本轮发言走完后再取一次插话:接住"最后一个人发言之后才到"的插话(否则它卡在收敛前、没人响应=石沉大海)
        for _插 in _取新插话(插话源):
            if _是叫停(_插):
                全部.append({"发言人": "船主", "类型": "叫停", "内容": _插})
                _进度(progress, f"⏸ 船主叫停，会议中止：{_插}")
                return 全部, [f"船主已叫停本次会议：{_插}"]
            全部.append({"发言人": "船主", "类型": "插话", "内容": _插})
            本轮有新插话 = True
            _进度(progress, f"🗣 船主插话：{_插}（全员下一轮回应、调整）")

        评 = await _评估收敛(全部)
        缺 = 评.get("缺证据") or []
        if 缺:
            for f in 缺:
                内容 = _尝试查阅(str(f))
                if 内容:
                    全部.append({"发言人": "资料室", "类型": "已查阅", "内容": f"【{f}】（按申请查阅）\n{内容[:3000]}"})
                    _进度(progress, f"按申请查阅：{f}，内容已补进会议。")
                else:
                    待船主问题.append(str(f))
            if 待船主问题:
                全部.append({"发言人": "项目经理", "类型": "要问船主",
                            "内容": f"有几处得先问船主才好往下定：{'、'.join(待船主问题)}。先停在这儿等船主回话——不硬编、不硬出工单。"})
                _进度(progress, f"要问船主：{'、'.join(待船主问题)}，圆桌暂停等船主回话。")
                收敛 = {"够了": False, "理由": "待船主回话"}
                break
            continue

        # 船主插话=掌舵:本轮有过插话→哪怕评估"够了",也强制再走一轮,让全员都把船主的话纳入、调整后再收敛
        if 本轮有新插话 and 轮 < max(1, 上限):
            全部.append({"发言人": "项目经理", "类型": "插话响应",
                        "内容": "船主中途有新指示，安排大家下一轮都按新指示回应、调整后再定收敛。"})
            _进度(progress, "🗣 船主插话已收到，安排全员下一轮回应、调整后再收敛。")
            continue

        全部.append({"发言人": "项目经理", "类型": "收敛判断",
                    "内容": f"（第{轮}轮后）{'够了，可以收敛' if 评.get('够了') else '还没够，再聊一轮'}：{评.get('理由', '')}"})
        收敛 = {"够了": bool(评.get("够了")), "理由": str(评.get("理由", ""))}
        _进度(progress, f"第{轮}轮后项目经理评估：{'够了，收敛' if 收敛['够了'] else '没够，再聊一轮'}——{收敛.get('理由', '')}")
        if 收敛["够了"]:
            break

    if not 收敛["够了"] and 收敛.get("理由") != "待船主回话":
        全部.append({"发言人": "项目经理", "类型": "未谈拢",
                    "内容": f"聊了{轮}轮仍未谈拢（{收敛.get('理由', '')}）。按现有讨论先收尾，建议船主介入定夺。"})
    if not 待船主问题:  # 正常收敛/收尾（非暂停要问船主）→ 写「会议小结」永久留痕：大厅显示开了几轮、谁参会，刷新也在
        全部.append({"发言人": "项目经理", "类型": "会议小结",
                    "内容": f"本场会议经 {轮} 轮讨论收敛，参会 {len(roles)} 人（{'、'.join(roles)}）。"})
    return 全部, 待船主问题


async def _汇总(需求: str, plan: dict[str, Any], speeches: list[dict[str, str]]) -> dict[str, Any]:
    final_prompt = (
        "你是开发公司的项目经理，给这场会收尾。先判这场会是哪一种：\n"
        "· 【开发活】(做个功能/写个东西) → 形成决议 + 拆成子工单。\n"
        "· 【评估/讨论/咨询】(评测某岗、讨论哪个方案好、帮船主拿主意) → **绝不拆工单(子工单留空[])**，"
        "重点写好『项目经理结论』：讨论得出了什么、你怎么定，并在里面说清『下一步建议』——这事该派给谁做 / 不用做 / 还需要船主拍板什么(像真公司开完会拍板那样)。\n"
        "只输出JSON对象，不要Markdown、不要解释。\n"
        "JSON字段: 标题, 任务类型(\"开发\"或\"评估讨论\"), 覆盖说明, 项目经理结论, 决议, 验收标准, 资料需求, 子工单。\n"
        "『项目经理结论』用大白话、说人话、别列条，但**必须有实质、不许空泛和稀泥**——讲清这四样："
        "①这场会磨出了什么共识 ②谁的观点被谁说动了/最后采纳了谁的哪点(收敛是论证磨出来的,不是各加一句) "
        "③最后拍板定什么、为什么这么定 ④下一步派给谁做。"
        "**严禁**『大家讨论充分、已形成共识』这种看了等于没看的套话。\n"
        "评估讨论类：决议/验收/资料需求/子工单都留空，把劲全使在『项目经理结论』上。\n"
        "开发类子工单元素字段: 标题,岗位,任务,白名单(数组),依赖(数组,可空),预算步数(整数),预算依据,验收命令,会议依据；"
        "岗位只能从 项目经理/首席工程师/工程师/文案工程师（兼职）/测试工程师 中选；白名单逐条列出。\n\n"
        f"船主需求:\n{需求}\n\n会前议题:\n{_紧凑(plan, 4000)}\n\n岗位发言:\n{_紧凑(speeches, 10000)}"
    )
    raw = await _问项目经理(final_prompt, 最大tokens=6144)
    try:
        return _提取_json(raw)
    except Exception:  # noqa: BLE001
        raw2 = await _问项目经理(final_prompt + "\n\n上次输出不是合法JSON。请立刻重试，只输出JSON，不要解释。", 最大tokens=6144)
        return _提取_json(raw2)


async def 分解(需求: str, progress=None, 插话源=None, 续档: dict[str, Any] | None = None) -> dict[str, Any]:
    if 续档:  # 债⑥:续会从断点续——复用已存的议题/参会/发言,注入船主回话后接着讨论,不重跑前面
        plan = {"议题": 续档.get("议题") or 需求, "讨论问题": []}
        roles = [r for r in (续档.get("roles") or []) if r in 可参会岗位] or ["文案工程师（兼职）", "测试工程师"]
        起点 = list(续档.get("已有发言") or []) + [
            {"发言人": "船主", "类型": "插话", "内容": "【回话】" + str(续档.get("回应") or "")}]
        _进度(progress, "收到你的回话，圆桌从断点接着开（不重跑前面的发言）。")
        speeches, 待船主 = await _圆桌讨论(需求, plan, roles, progress=progress, 插话源=插话源, 已有发言=起点)
    else:
        _进度(progress, "项目经理正在准备项目会议题。")
        _进度(progress, {"类型": "发言开始", "发言人": "项目经理"})  # 议题准备:建项目经理斟酌气泡,思考/工具亮在里面(治58秒黑箱)
        plan = await _会前议题(需求, progress=progress)
        if 巧.急停文件.exists():  # 瞬时停止键:在"准备议题"阶段就被停→干净结束,不继续往下开会(船主撞的就是这个盲区)
            _进度(progress, "⏸ 已停止本次会议。")
            return {"已停止": True, "标题": str(plan.get("标题") or "会议"), "覆盖说明": "已停止本次会议", "子工单": []}
        roles = [x for x in _去重([str(x) for x in plan.get("参会", [])]) if x in 可参会岗位] or ["文案工程师（兼职）", "测试工程师"]
        _进度(progress, {"类型": "发言结束", "发言人": "项目经理",
                        "内容": f"议题已拟好：{plan.get('标题') or '项目会'}。参会：{'、'.join(roles)}"})  # 定格斟酌气泡
        _进度(progress, "项目会参会岗位：" + "、".join(roles))
        speeches, 待船主 = await _圆桌讨论(需求, plan, roles, progress=progress, 插话源=插话源)
    if 待船主 == ["__停止__"]:  # 瞬时停止键:干净结束,不出工单、不建待续(船主要的是终止+待命,不是暂停可续)
        return {"已停止": True, "标题": str(plan.get("标题") or "会议"), "覆盖说明": "已停止本次会议", "子工单": []}
    if 待船主:
        _进度(progress, "圆桌暂停：有几处要先问船主，等船主回话后续会。")
        return {"待船主": 待船主, "标题": str(plan.get("标题") or "办公室任务"),
                "覆盖说明": "圆桌暂停：岗位有几处得先问船主才好往下定，已停下等船主回话。",
                "项目会": {"议题": plan.get("议题") or "项目会",
                          "参会": _去重(["船主", "项目经理", *roles]),
                          "发言": [{"发言人": "船主", "类型": "发言", "内容": 需求},
                                  {"发言人": "项目经理", "类型": "开场",
                                   "内容": str(plan.get("主持说明") or plan.get("覆盖说明") or "")}, *speeches],
                          "决议": [], "验收标准": [], "资料需求": plan.get("资料需求") or []},
                "子工单": []}
    if 巧.急停文件.exists():  # 瞬时停止键:汇总前被停→干净结束,不出工单
        _进度(progress, "⏸ 已停止本次会议。")
        return {"已停止": True, "标题": str(plan.get("标题") or "会议"), "覆盖说明": "已停止本次会议", "子工单": []}
    _进度(progress, "项目经理正在汇总会议发言，形成决议和工单。")
    final = await _汇总(需求, plan, speeches)
    meeting = {
        "议题": plan.get("议题") or final.get("标题") or "项目会",
        "参会": _去重(["船主", "项目经理", *roles]),
        "发言": [
            {"发言人": "船主", "类型": "发言", "内容": 需求},
            {"发言人": "项目经理", "类型": "开场",
             "内容": str(plan.get("主持说明") or plan.get("覆盖说明") or "先讨论需求、资料、风险、标准和岗位分工，再形成决议。")},
            *speeches,
            {"发言人": "项目经理", "类型": "结论", "内容": str(final.get("项目经理结论") or final.get("覆盖说明") or "已形成会议决议和待确认工单。")},
        ],
        "决议": final.get("决议") or [], "验收标准": final.get("验收标准") or [],
        "资料需求": final.get("资料需求") or plan.get("资料需求") or [],
    }
    data = {
        "标题": final.get("标题") or plan.get("标题") or "办公室任务",
        "覆盖说明": final.get("覆盖说明") or plan.get("覆盖说明") or "项目会已形成决议。",
        "项目会": meeting, "子工单": final.get("子工单") or [],
        "评估结论": str(final.get("项目经理结论") or "") if not (final.get("子工单") or []) else "",
    }
    if data["子工单"]:  # 开发类:校验工单格式
        return _校验(data)
    return data  # ③评估/讨论类:不出工单、不走工单校验(否则会崩在"子工单为空"),直接给结论


async def kickoff(需求: str, progress=None, 插话源=None, 续档: dict[str, Any] | None = None) -> dict[str, Any]:
    初始化()
    data = await 分解(需求, progress=progress, 插话源=插话源, 续档=续档)
    if data.get("已停止"):  # 瞬时停止键:不落会议文件、不出工单,干净结束
        return {"会议": None, "工单": [], "已停止": True, "覆盖说明": "已停止本次会议"}
    if data.get("待船主"):
        return _暂停存盘(需求, data, progress)
    idx = _编号()
    title = _安全名(str(data["标题"]))
    meeting = 会议目录 / f"{idx:03d}-{title}.md"
    lines = [f"# 项目会 {idx:03d} · {data['标题']}（AgentScope）",
             f"时间: {dt.datetime.now().isoformat(timespec='seconds')}", "",
             "## 原始需求", 需求, "", "## 覆盖说明", str(data["覆盖说明"]), "",
             "## 项目会", _渲染项目会(data["项目会"]), "", "## 子工单"]
    names: list[str] = []
    task_records: list[dict[str, Any]] = []
    for i, item in enumerate(data["子工单"], 1):
        name = f"AS-{idx:03d}-{i}_{_安全名(str(item['标题']))}.md"
        names.append(name)
        task_records.append({**item, "文件": name})
        lines.append(f"- `{name}` ｜ {item['岗位']} ｜ 依据: {item.get('会议依据', '') or '未写'} ｜ 依赖: {', '.join(item.get('依赖') or []) or '无'}")
        _写子工单(待确认 / name, item, meeting.name)
        _进度(progress, f"已生成待确认工单：{name}")
    meeting.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        import 协同
        协同.记录项目会(meeting.name, str(data["标题"]), 需求, str(data["覆盖说明"]), data["项目会"], task_records)
    except Exception:  # noqa: BLE001
        pass
    return {"会议": meeting.name, "工单": names, "覆盖说明": data["覆盖说明"], "评估结论": data.get("评估结论", "")}


def _暂停存盘(需求: str, data: dict[str, Any], progress=None) -> dict[str, Any]:
    """圆桌要问船主→写会议纪要(含问题)+存待续状态(原需求),等船主回话后续会。"""
    初始化()
    idx = _编号()
    title = _安全名(str(data.get("标题") or "项目会"))
    meeting = 会议目录 / f"{idx:03d}-{title}.md"
    问题 = data.get("待船主") or []
    lines = [f"# 项目会 {idx:03d} · {data.get('标题')}（待船主回话）",
             f"时间: {dt.datetime.now().isoformat(timespec='seconds')}", "",
             "## 原始需求", 需求, "", "## 覆盖说明", str(data.get("覆盖说明", "")), "",
             "## 项目会", _渲染项目会(data["项目会"]), "",
             "## 要问船主（你在项目室回话后自动续会）", *[f"- {q}" for q in 问题]]
    meeting.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _项目会 = data.get("项目会", {})
    _roles = [r for r in _项目会.get("参会", []) if r not in ("船主", "项目经理")]
    (会议目录 / f"{meeting.stem}.待续.json").write_text(
        json.dumps({"需求": 需求, "问题": 问题,
                    "已有发言": _项目会.get("发言", []),  # 债⑥:存发言流,续会从这接着,不重跑
                    "议题": _项目会.get("议题") or 需求, "roles": _roles},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        import 协同
        协同.记录项目会(meeting.name, str(data["标题"]), 需求, str(data.get("覆盖说明", "")), data["项目会"], [])
    except Exception:  # noqa: BLE001
        pass
    _进度(progress, "已停在『要问船主』，等你在项目室回话后自动续会。")
    return {"会议": meeting.name, "工单": [], "待船主": 问题, "待回应": True, "覆盖说明": data.get("覆盖说明", "")}


def 续会(会议名: str, 船主回应: str, progress=None) -> dict[str, Any]:
    """债⑥:船主在项目室回话 → 读出已有发言流 + 注入回话 → 从断点接着开(不重跑前面的发言)。"""
    stem = 会议名.rsplit(".", 1)[0] if 会议名.endswith(".md") else 会议名
    st = 会议目录 / f"{stem}.待续.json"
    续档: dict[str, Any] = {}
    if st.exists():
        try:
            续档 = json.loads(st.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            续档 = {}
        try:
            st.unlink()
        except Exception:  # noqa: BLE001
            pass
    续档["回应"] = 船主回应
    需求 = str(续档.get("需求") or "")
    if not 续档.get("已有发言"):  # 老格式无发言流(旧待续)→退回重开,不崩(向后兼容)
        新需求 = (需求 + "\n\n【船主回话】" + 船主回应) if 需求 else 船主回应
        return 运行(新需求, progress=progress)
    return asyncio.run(kickoff(需求, progress=progress, 续档=续档))


def 运行(需求: str, progress=None, 插话源=None) -> dict[str, Any]:
    return asyncio.run(kickoff(需求, progress=progress, 插话源=插话源))


if __name__ == "__main__":
    import sys
    print(运行(sys.argv[1] if len(sys.argv) > 1 else "写一份公司公告，说明 AgentScope 迁移已启动。"))
